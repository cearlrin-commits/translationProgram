"""번역문을 원본 문단과 같은 위치·크기·색으로 배치한다."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

import pymupdf as fitz

from .fonts import KoreanFonts
from .models import Color, PageContent, Paragraph, Rect

# 줄 맨 앞에 오면 안 되는 문장부호 / 줄 맨 끝에 오면 안 되는 여는 부호
_NO_LINE_START = set(".,;:!?)]}%’”」』〉》、。·…")
_NO_LINE_END = set("([{‘“「『〈《")
_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9.,'’\-/:]*|\s+|.", re.S)


@dataclass
class LayoutParams:
    """문단 하나의 배치 설정. 검증 결과에 따라 fixer가 값을 고친다."""

    dx: float = 0.0  # 위치 보정
    dy: float = 0.0
    size_override: Optional[float] = None  # 글자 크기를 강제로 지정(테스트/수동)
    size_correction: float = 1.0  # 측정값과 어긋날 때의 보정 배율
    pitch_correction: float = 1.0
    bottom_shrink: float = 0.0  # 다른 요소와 겹칠 때 허용 영역 축소
    allow_extend: bool = True  # 아래 빈 공간 사용 허용
    redact_pad: float = 0.0  # 원문 지우기 영역 여유


@dataclass
class Region:
    left: float
    right: float
    top: float
    bottom: float  # 원본 문단의 아래쪽
    free_bottom: float  # 빈 공간까지 포함한 최대 아래쪽
    left_limit: float  # 한 줄짜리 문단의 가로 확장 한계
    right_limit: float


@dataclass
class PlacedLine:
    text: str
    x: float
    baseline: float
    width: float
    justify_width: Optional[float] = None


@dataclass
class Plan:
    paragraph_id: str
    lines: list[PlacedLine]
    font_size: float
    line_pitch: float
    bold: bool
    color: Color
    fits: bool
    adjustments: list[str] = field(default_factory=list)

    @property
    def bottom(self) -> float:
        return self.lines[-1].baseline if self.lines else 0.0


def compute_regions(page: PageContent) -> dict[str, Region]:
    paras = page.paragraphs
    if not paras:
        return {}
    content_left = min(p.bbox[0] for p in paras)
    content_right = max(p.bbox[2] for p in paras)
    content_bottom = max(p.bbox[3] for p in paras)
    page_bottom = max(content_bottom, page.height - 24)
    regions: dict[str, Region] = {}
    for p in paras:
        x0, y0, x1, y1 = p.bbox
        blockers = [q.bbox for q in paras if q is not p] + list(page.obstacles)
        below = [b[1] for b in blockers if b[1] >= y1 - 1 and b[0] < x1 - 1 and b[2] > x0 + 1]
        gap = max(2.0, p.font_size * 0.3)
        free_bottom = min(below) - gap if below else page_bottom
        side = [b for b in blockers if b[1] < y1 - 1 and b[3] > y0 + 1]
        right_limit = min([b[0] - 4 for b in side if b[0] >= x1 - 1] + [max(content_right, x1)])
        left_limit = max([b[2] + 4 for b in side if b[2] <= x0 + 1] + [min(content_left, x0)])
        regions[p.id] = Region(x0, x1, y0, y1, max(y1, free_bottom), left_limit, right_limit)
    return regions


def wrap(text: str, fonts: KoreanFonts, size: float, bold: bool, first_width: float, width: float, mode: str) -> list[str]:
    """mode="word": 띄어쓰기 단위 줄바꿈, mode="char": 글자 단위 줄바꿈(영문 단어·숫자는 유지)."""
    if mode == "word":
        tokens = re.findall(r"\S+|\s+", text)
        # 한 단어가 줄보다 길면 글자 단위로 쪼갠다
        out: list[str] = []
        for t in tokens:
            if not t.isspace() and fonts.width(t, size, bold) > width:
                out.extend(_TOKEN.findall(t))
            else:
                out.append(t)
        tokens = out
    else:
        tokens = _TOKEN.findall(text)
    tokens = _attach_punctuation(tokens)

    lines: list[str] = []
    cur = ""
    limit = first_width
    for tok in tokens:
        if tok.isspace():
            if cur:
                cur += " "
            continue
        candidate = cur + tok
        if cur and fonts.width(candidate.rstrip(), size, bold) > limit + 0.01:
            lines.append(cur.rstrip())
            limit = width
            cur = tok
        else:
            cur = candidate
    if cur.strip():
        lines.append(cur.rstrip())
    return lines


def _attach_punctuation(tokens: list[str]) -> list[str]:
    out: list[str] = []
    for tok in tokens:
        if out and not tok.isspace() and tok[0] in _NO_LINE_START and not out[-1].isspace():
            out[-1] += tok
        elif out and not tok.isspace() and out[-1] and out[-1][-1] in _NO_LINE_END:
            out[-1] += tok
        else:
            out.append(tok)
    return out


class LayoutEngine:
    def __init__(self, fonts: KoreanFonts):
        self.fonts = fonts

    # ---- 배치 계획 -------------------------------------------------------
    def plan(self, para: Paragraph, text: str, region: Region, params: LayoutParams) -> Plan:
        """원본 글자 크기를 최대한 유지하면서 원본 영역 안에 들어가도록 배치 방법을 고른다."""
        size0 = params.size_override or para.font_size
        pitch0 = para.line_pitch * (size0 / para.font_size)
        bottom_free = (region.free_bottom if params.allow_extend else region.bottom) - params.bottom_shrink
        bottom_orig = region.bottom - params.bottom_shrink

        attempts: list[tuple[str, str, float, float, float]] = [
            ("", "word", size0, pitch0, bottom_orig),
            ("글자 단위 줄바꿈", "char", size0, pitch0, bottom_orig),
        ]
        if bottom_free > bottom_orig + 0.5:
            attempts.append(("아래 빈 공간 사용", "char", size0, pitch0, bottom_free))
        for factor in (0.95, 0.9):
            pitch = max(pitch0 * factor, size0 * 1.08)
            if pitch < pitch0 - 0.1:
                attempts.append((f"줄 간격 축소({pitch:.1f}pt)", "char", size0, pitch, bottom_free))
        if params.size_override is None:
            size = size0
            while size > para.font_size * 0.6:
                size = round(size - 0.25, 2)
                pitch = max(pitch0 * size / size0 * 0.9, size * 1.08)
                attempts.append((f"글자 크기 축소({size:.2f}pt)", "char", size, pitch, bottom_free))

        plan = None
        for note, mode, size, pitch, bottom in attempts:
            plan = self._build(para, text, region, params, size, pitch, mode, bottom)
            if note:
                plan.adjustments.append(note)
            if plan.fits:
                return plan
        plan.adjustments.append("영역에 다 들어가지 않음")
        return plan

    def _build(self, para: Paragraph, text: str, region: Region, params: LayoutParams,
               size: float, pitch: float, mode: str, bottom: float) -> Plan:
        size_draw = size * params.size_correction
        pitch *= params.pitch_correction
        left, right = region.left, region.right
        single = len(para.lines) == 1
        if single:
            # 한 줄짜리(제목 등)는 옆이 비어 있으면 가로로 조금 더 써도 된다
            if para.align == "center":
                c = (left + right) / 2
                half = min(c - region.left_limit, region.right_limit - c)
                left, right = c - half, c + half
            elif para.align == "right":
                left = region.left_limit
            else:
                right = region.right_limit
        width = right - left
        indent = para.first_indent
        lines = wrap(text, self.fonts, size_draw, para.bold, width - indent, width, mode)

        scale = size / para.font_size
        first_baseline = region.top + (para.first_baseline - region.top) * scale + params.dy
        placed: list[PlacedLine] = []
        for i, line in enumerate(lines):
            w = self.fonts.width(line, size_draw, para.bold)
            if para.align == "center":
                x = left + (width - w) / 2
            elif para.align == "right":
                x = right - w
            else:
                x = left + (indent if i == 0 else 0)
            justify = None
            if para.align == "justify" and i < len(lines) - 1 and " " in line:
                avail = right - x
                if avail - w < size * 3:  # 너무 많이 벌어지면 양쪽 정렬하지 않는다
                    justify = avail
            placed.append(PlacedLine(line, x + params.dx, first_baseline + i * pitch, w, justify))

        last = placed[-1].baseline if placed else first_baseline
        text_bottom = last + self.fonts.descent(para.bold) * size_draw
        too_wide = any(pl.width > (right - left) + 0.5 for pl in placed)
        fits = text_bottom <= bottom + 1.0 and not too_wide
        return Plan(para.id, placed, size, pitch, para.bold, para.color, fits)

    # ---- 그리기 ----------------------------------------------------------
    def render(self, page: fitz.Page, plan: Plan, params: LayoutParams) -> None:
        size = plan.font_size * params.size_correction
        writer = fitz.TextWriter(page.rect)
        for line in plan.lines:
            if line.justify_width:
                words = line.text.split(" ")
                natural = sum(self.fonts.width(w, size, plan.bold) for w in words)
                gap = (line.justify_width - natural) / (len(words) - 1)
                x = line.x
                for i, word in enumerate(words):
                    # 띄어쓰기 글자도 함께 넣어야 복사/검색할 때 단어가 붙지 않는다
                    spaced = word + " " if i < len(words) - 1 else word
                    self._append(writer, spaced, x, line.baseline, size, plan.bold)
                    x += self.fonts.width(word, size, plan.bold) + gap
            else:
                self._append(writer, line.text, line.x, line.baseline, size, plan.bold)
        writer.write_text(page, color=plan.color)

    def _append(self, writer: fitz.TextWriter, text: str, x: float, baseline: float, size: float, bold: bool) -> None:
        for font, run in self.fonts.runs(text, bold):
            writer.append((x, baseline), run, font=font, fontsize=size)
            x += font.text_length(run, fontsize=size)


def erase_original_text(page: fitz.Page, paragraphs: list[Paragraph], params: dict[str, LayoutParams]) -> None:
    """번역할 문단의 원문 글자만 지운다(배경·이미지·선은 그대로 둔다)."""
    count = 0
    for para in paragraphs:
        pad = params[para.id].redact_pad
        for x0, y0, x1, y1 in para.span_rects:
            inset = (y1 - y0) * 0.15
            rect = fitz.Rect(x0 - pad, y0 + inset - pad, x1 + pad, y1 - inset + pad)
            if not rect.is_empty:
                page.add_redact_annot(rect, fill=False)
                count += 1
    if count:
        page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE, graphics=fitz.PDF_REDACT_LINE_ART_NONE)


def union(rects: list[Rect]) -> Rect:
    return (min(r[0] for r in rects), min(r[1] for r in rects), max(r[2] for r in rects), max(r[3] for r in rects))
