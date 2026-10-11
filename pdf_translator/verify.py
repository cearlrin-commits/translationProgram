"""배치 검증: 번역본 PDF를 다시 읽어 원본과 글자 크기·위치·영역을 비교한다.

배치 계획(Plan)을 믿지 않고 실제로 만들어진 PDF에서 글자를 다시 추출해 비교하므로,
글꼴 대체, 지우지 못한 원문, 영역 넘침 같은 실제 결과물의 문제를 잡아낸다.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass

import pymupdf as fitz

from .fonts import FONT_NAMES
from .layout import Region
from .models import PageContent, TranslatedParagraph

SIZE_TOL = 0.25  # pt
POS_TOL = 1.0  # pt
COLOR_TOL = 0.03


@dataclass
class LayoutDiff:
    paragraph_id: str
    kind: str  # font_size | position_x | position_y | line_pitch | color | overflow | overflow_x | overlap | missing_text | original_text_left | extends_below
    expected: float | str
    actual: float | str
    message: str

    @property
    def is_hard(self) -> bool:
        # extends_below: 원본 영역 아래 빈 공간을 쓴 것. 다른 요소와 겹치지 않으므로 정보로만 남긴다.
        return self.kind != "extends_below"


@dataclass
class _Placed:
    text: str
    size: float
    color: tuple[float, float, float]
    bbox: tuple[float, float, float, float]
    origin: tuple[float, float]


def _is_ours(font: str) -> bool:
    return any(name.replace(" ", "") in font.replace(" ", "") for name in FONT_NAMES)


def read_rendered(page: fitz.Page) -> tuple[list[_Placed], list[_Placed]]:
    """페이지의 글자를 (우리가 넣은 한국어, 원래 있던 글자)로 나눈다."""
    ours, others = [], []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            for s in line["spans"]:
                if not s["text"].strip():
                    continue
                c = s["color"]
                item = _Placed(s["text"], s["size"], ((c >> 16 & 255) / 255, (c >> 8 & 255) / 255, (c & 255) / 255),
                               tuple(s["bbox"]), tuple(s["origin"]))
                (ours if _is_ours(s["font"]) else others).append(item)
    return ours, others


def _inside(pt, rect, tol=1.0) -> bool:
    x, y = pt
    return rect[0] - tol <= x <= rect[2] + tol and rect[1] - tol <= y <= rect[3] + tol


def _intersects(a, b, shrink=1.0) -> bool:
    return a[0] < b[2] - shrink and a[2] > b[0] + shrink and a[1] < b[3] - shrink and a[3] > b[1] + shrink


def verify_page(page: fitz.Page, content: PageContent, translations: dict[str, TranslatedParagraph],
                regions: dict[str, Region]) -> list[LayoutDiff]:
    ours, others = read_rendered(page)
    diffs: list[LayoutDiff] = []
    targets = [p for p in content.paragraphs if p.id in translations]

    # 각 한국어 조각을 허용 영역에 기준선 시작점이 들어가는 문단에 배정한다
    assigned: dict[str, list[_Placed]] = {p.id: [] for p in targets}
    for item in ours:
        center = ((item.bbox[0] + item.bbox[2]) / 2, item.origin[1])
        for p in targets:
            r = regions[p.id]
            area = (min(r.left, r.left_limit), r.top, max(r.right, r.right_limit), r.free_bottom)
            if _inside(center, area, tol=2.0):
                assigned[p.id].append(item)
                break

    for p in targets:
        items = assigned[p.id]
        expected_text = translations[p.id].text
        if not items:
            diffs.append(LayoutDiff(p.id, "missing_text", len(expected_text), 0, "번역문이 페이지에 그려지지 않았습니다."))
            continue
        x0, y0, x1, y1 = p.bbox
        r = regions[p.id]

        drawn = sum(len(re.sub(r"\s", "", i.text)) for i in items)
        want = len(re.sub(r"\s", "", expected_text))
        if drawn < want:
            diffs.append(LayoutDiff(p.id, "missing_text", want, drawn, f"글자 {want - drawn}개가 그려지지 않았습니다."))

        size = statistics.median(i.size for i in items)
        if abs(size - p.font_size) > SIZE_TOL:
            diffs.append(LayoutDiff(p.id, "font_size", p.font_size, round(size, 2),
                                    f"글자 크기가 원본({p.font_size:.2f}pt)과 다릅니다({size:.2f}pt)."))

        # 줄 단위로 묶기(같은 기준선)
        baselines = sorted({round(i.origin[1], 1) for i in items})
        lines = [[i for i in items if abs(i.origin[1] - b) < 0.6] for b in baselines]
        first = lines[0]
        fx0 = min(i.bbox[0] for i in first)
        fx1 = max(i.bbox[2] for i in first)
        if p.align == "center":
            exp_x = (p.lines[0].bbox[0] + p.lines[0].bbox[2]) / 2
            act_x = (fx0 + fx1) / 2
            # 가운데 정렬은 글자 폭이 달라지므로 중심 위치만 비교한다
        elif p.align == "right":
            exp_x, act_x = p.lines[0].bbox[2], fx1
        else:
            exp_x, act_x = p.lines[0].bbox[0], fx0
        if abs(act_x - exp_x) > POS_TOL:
            diffs.append(LayoutDiff(p.id, "position_x", round(exp_x, 2), round(act_x, 2),
                                    f"첫 줄 가로 위치가 {act_x - exp_x:+.2f}pt 어긋났습니다."))
        # 크기를 줄인 경우 위쪽(글자 윗선)이 같은 자리에 오도록 기준선이 비례해서 올라간다
        exp_y = y0 + (p.first_baseline - y0) * (size / p.font_size)
        act_y = first[0].origin[1]
        if abs(act_y - exp_y) > POS_TOL:
            diffs.append(LayoutDiff(p.id, "position_y", round(exp_y, 2), round(act_y, 2),
                                    f"첫 줄 기준선이 {act_y - exp_y:+.2f}pt 어긋났습니다."))

        if len(baselines) >= 2 and len(p.lines) >= 2:
            pitch = statistics.median(b - a for a, b in zip(baselines, baselines[1:]))
            exp_pitch = p.line_pitch * size / p.font_size
            if abs(pitch - exp_pitch) > 0.5:
                diffs.append(LayoutDiff(p.id, "line_pitch", round(exp_pitch, 2), round(pitch, 2),
                                        f"줄 간격이 원본({exp_pitch:.2f}pt)과 다릅니다({pitch:.2f}pt)."))

        colors = [i.color for i in items]
        if any(max(abs(a - b) for a, b in zip(c, p.color)) > COLOR_TOL for c in colors):
            diffs.append(LayoutDiff(p.id, "color", str(p.color), str(colors[0]), "글자 색이 원본과 다릅니다."))

        ux0 = min(i.bbox[0] for i in items)
        ux1 = max(i.bbox[2] for i in items)
        uy1 = max(i.bbox[3] for i in items)
        h_left = min(x0, r.left_limit) if len(p.lines) == 1 else x0
        h_right = max(x1, r.right_limit) if len(p.lines) == 1 else x1
        if ux0 < h_left - 1.5 or ux1 > h_right + 1.5:
            diffs.append(LayoutDiff(p.id, "overflow_x", f"{h_left:.1f}~{h_right:.1f}", f"{ux0:.1f}~{ux1:.1f}",
                                    "번역문이 원본 문단 영역 옆으로 넘쳤습니다."))
        if uy1 > r.free_bottom + 1.5:
            diffs.append(LayoutDiff(p.id, "overflow", round(r.free_bottom, 2), round(uy1, 2),
                                    "번역문이 아래 요소/페이지 끝까지 넘쳤습니다."))
        elif uy1 > y1 + 1.5:
            diffs.append(LayoutDiff(p.id, "extends_below", round(y1, 2), round(uy1, 2),
                                    f"원본 영역보다 {uy1 - y1:.1f}pt 아래까지 썼습니다(빈 공간)."))

        for item in items:
            for q in content.paragraphs:
                if q.id != p.id and _intersects(item.bbox, q.bbox):
                    diffs.append(LayoutDiff(p.id, "overlap", q.id, item.text[:20], f"문단 {q.id}와 겹칩니다."))
                    break
            else:
                continue
            break

        leftover = [o.text for o in others if any(_intersects(o.bbox, sr, 0.5) for sr in p.span_rects)]
        if leftover:
            diffs.append(LayoutDiff(p.id, "original_text_left", "", " ".join(leftover)[:40],
                                    "원문 글자가 지워지지 않고 남아 있습니다."))
    return diffs
