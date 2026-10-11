"""PDF 페이지에서 텍스트를 인식해 문단(배치 단위) 목록으로 만든다."""

from __future__ import annotations

import re
import statistics
import unicodedata

import pymupdf as fitz

from .models import Line, PageContent, Paragraph, Span
from .segment import split_sentences

_TEXT_FLAGS = fitz.TEXT_PRESERVE_WHITESPACE | fitz.TEXT_MEDIABOX_CLIP


def extract_document(doc: fitz.Document, pages: list[int] | None = None) -> list[PageContent]:
    numbers = pages if pages is not None else range(len(doc))
    return [extract_page(doc[n]) for n in numbers]


def extract_page(page: fitz.Page) -> PageContent:
    data = page.get_text("dict", flags=_TEXT_FLAGS)
    paragraphs: list[Paragraph] = []
    for block in data["blocks"]:
        if block.get("type") != 0:
            continue
        lines = _block_lines(block)
        for group in _split_paragraphs(lines):
            para = _make_paragraph(page.number, len(paragraphs), group, page.rect.width)
            if para is not None:
                paragraphs.append(para)

    obstacles = [tuple(img["bbox"]) for img in page.get_image_info()]
    for d in page.get_drawings():
        r = d["rect"]
        if r.width > 0.5 or r.height > 0.5:
            obstacles.append((r.x0, r.y0, r.x1, r.y1))
    return PageContent(page.number, page.rect.width, page.rect.height, paragraphs, obstacles)


def _block_lines(block: dict) -> list[Line]:
    lines: list[Line] = []
    for raw in block["lines"]:
        dx, dy = raw["dir"]
        if abs(dy) > 0.01 or dx < 0:  # 세로/회전된 글자는 원본 그대로 둔다
            continue
        spans = [_span(s) for s in raw["spans"] if s["text"].strip()]
        if not spans:
            continue
        line = Line(spans, tuple(raw["bbox"]), raw["spans"][0]["origin"][1])
        prev = lines[-1] if lines else None
        # 같은 기준선에서 넓은 간격 때문에 쪼개진 줄은 하나로 합친다
        if prev and abs(prev.baseline - line.baseline) < 1.5 and line.bbox[0] >= prev.bbox[2] - 1:
            prev.spans.extend(spans)
            prev.bbox = _union(prev.bbox, line.bbox)
        else:
            lines.append(line)
    return lines


def _span(s: dict) -> Span:
    flags = s["flags"]
    font = s["font"]
    c = s["color"]
    return Span(
        text=unicodedata.normalize("NFKC", s["text"]),
        size=round(s["size"], 2),
        font=font,
        bold=bool(flags & 16) or bool(re.search(r"bold|black|heavy", font, re.I)),
        italic=bool(flags & 2) or bool(re.search(r"italic|oblique", font, re.I)),
        color=((c >> 16 & 255) / 255, (c >> 8 & 255) / 255, (c & 255) / 255),
        bbox=tuple(s["bbox"]),
    )


def _line_size(line: Line) -> float:
    return _dominant(line.spans, lambda s: s.size)


def _line_bold(line: Line) -> bool:
    return _dominant(line.spans, lambda s: s.bold)


def _dominant(spans: list[Span], key):
    weights: dict = {}
    for s in spans:
        weights[key(s)] = weights.get(key(s), 0) + len(s.text.strip())
    return max(weights, key=weights.get)


def _split_paragraphs(lines: list[Line]) -> list[list[Line]]:
    """글자 크기/굵기가 바뀌거나 줄 사이가 크게 벌어지면 새 문단으로 본다."""
    groups: list[list[Line]] = []
    for line in lines:
        if groups:
            cur = groups[-1]
            prev = cur[-1]
            size = _line_size(line)
            prev_size = _line_size(prev)
            gap = line.bbox[1] - prev.bbox[3]
            same_style = abs(size - prev_size) <= 0.6 and _line_bold(line) == _line_bold(prev)
            pitch = line.baseline - prev.baseline
            expected_pitch = _pitch(cur) or prev_size * 1.25
            if same_style and gap < prev_size * 0.8 and pitch < expected_pitch * 1.4:
                cur.append(line)
                continue
        groups.append([line])
    return groups


def _pitch(lines: list[Line]) -> float | None:
    diffs = [b.baseline - a.baseline for a, b in zip(lines, lines[1:]) if b.baseline > a.baseline]
    return statistics.median(diffs) if diffs else None


def _join_lines(lines: list[Line]) -> str:
    text = ""
    for line in lines:
        t = re.sub(r"\s+", " ", line.text).strip()
        if not text:
            text = t
        elif re.search(r"[A-Za-z]-$", text) and t[:1].islower():
            text = text[:-1] + t  # 줄 끝 하이픈으로 끊긴 단어를 잇는다
        else:
            text += " " + t
    return text


def _detect_align(lines: list[Line], page_width: float) -> str:
    x0s = [ln.bbox[0] for ln in lines]
    x1s = [ln.bbox[2] for ln in lines]
    left, right = min(x0s), max(x1s)
    if len(lines) == 1:
        center = (x0s[0] + x1s[0]) / 2
        width = x1s[0] - x0s[0]
        if abs(center - page_width / 2) < page_width * 0.02 and width < page_width * 0.7:
            return "center"
        return "left"
    body = lines[1:]  # 첫 줄은 들여쓰기가 있을 수 있다
    body_left = all(abs(ln.bbox[0] - left) < 2 for ln in body)
    full = lines[:-1]
    full_right = all(abs(ln.bbox[2] - right) < 2.5 for ln in full)
    if body_left and full_right:
        return "justify"
    if body_left:
        return "left"
    centers = [(ln.bbox[0] + ln.bbox[2]) / 2 for ln in lines]
    if max(centers) - min(centers) < 3:
        return "center"
    if all(abs(x - right) < 2 for x in x1s):
        return "right"
    return "left"


def _make_paragraph(page_no: int, index: int, lines: list[Line], page_width: float) -> Paragraph | None:
    text = _join_lines(lines)
    if not text:
        return None
    spans = [s for ln in lines for s in ln.spans]
    size = _dominant(spans, lambda s: s.size)
    pitch = _pitch(lines) or round(size * 1.2, 2)
    align = _detect_align(lines, page_width)
    left = min(ln.bbox[0] for ln in lines)
    indent = lines[0].bbox[0] - left if len(lines) > 1 and align in ("left", "justify") else 0.0
    translatable = len(re.findall(r"[A-Za-z]", text)) >= 2
    return Paragraph(
        id=f"p{page_no + 1}-{index + 1}",
        page=page_no,
        lines=lines,
        text=text,
        font_size=size,
        color=_dominant(spans, lambda s: s.color),
        bold=_dominant(spans, lambda s: s.bold),
        align=align,
        line_pitch=pitch,
        first_indent=max(0.0, indent),
        translatable=translatable,
        sentences=split_sentences(text) if translatable else [],
    )


def _union(a, b):
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))
