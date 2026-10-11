"""테스트/시연용 영어 PDF를 만든다 (본문은 이 저장소를 위해 새로 쓴 글).

    python examples/make_sample.py examples/sample_en.pdf
"""

from __future__ import annotations

import sys

import pymupdf as fitz

TITLE = "Community Gardens in Small Cities"
SUBTITLE = "A short field report, 2025 season"

INTRO = [
    "Community gardens have become a common sight in small cities over the last decade. "
    "They turn empty lots into shared spaces where neighbors grow vegetables, herbs and flowers. "
    "This report looks at 12 gardens and describes what made them succeed.",
    "Most gardens started with a small group of volunteers and a loan of land from the city. "
    "In the first year, the average garden had 18 plots and a waiting list of about 9 families. "
    "By the third year, the number of plots had nearly doubled.",
]

SECTION = "1. What the gardeners told us"

BODY = [
    "We asked gardeners why they joined and what kept them coming back. "
    "The most frequent answer was the chance to meet people from the same street. "
    "Fresh food came second, and many people said that working outside helped them relax after work.",
    "Water was the biggest practical problem. "
    "Gardens that installed rain barrels early used 40% less city water during the summer. "
    "Shared tools were the second problem, because they were often lost or left out in the rain.",
]

NOTE_TITLE = "Key numbers"
NOTE = (
    "12 gardens visited. 214 active gardeners. "
    "Average plot size: 6.5 square meters. "
    "Typical yearly fee: 15 dollars."
)

LEFT_COL = (
    "Small cities have one clear advantage. "
    "Land is cheaper, and local officials are often easy to reach. "
    "Several gardens received permission within a few weeks."
)
RIGHT_COL = (
    "The main risk is that a garden depends on a few people. "
    "When a leader moves away, activity can drop quickly. "
    "Rotating roles every season helps to avoid this."
)

CLOSING = (
    "In short, a garden needs land, water and a group of people who share the work. "
    "With these three things in place, even a small lot can feed many families and bring a street together."
)


def build(path: str) -> None:
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    m = 60
    width = 595 - 2 * m

    page.insert_textbox(fitz.Rect(m, 60, 595 - m, 100), TITLE, fontname="hebo", fontsize=20, align=fitz.TEXT_ALIGN_CENTER)
    page.insert_textbox(fitz.Rect(m, 92, 595 - m, 120), SUBTITLE, fontname="heit", fontsize=11,
                        color=(0.4, 0.4, 0.4), align=fitz.TEXT_ALIGN_CENTER)

    y = 130
    for text in INTRO:
        y = _para(page, text, m, y, width) + 8

    page.insert_textbox(fitz.Rect(m, y, 595 - m, y + 30), SECTION, fontname="hebo", fontsize=13, color=(0.1, 0.3, 0.6))
    y += 24
    for text in BODY:
        y = _para(page, text, m, y, width) + 8

    # 배경색이 있는 상자(배경은 번역본에도 그대로 남아야 한다)
    box = fitz.Rect(m, y + 4, 595 - m, y + 74)
    page.draw_rect(box, color=(0.2, 0.45, 0.3), fill=(0.9, 0.96, 0.9), width=0.8)
    page.insert_textbox(fitz.Rect(box.x0 + 10, box.y0 + 8, box.x1 - 10, box.y0 + 30), NOTE_TITLE,
                        fontname="hebo", fontsize=11, color=(0.1, 0.35, 0.2))
    page.insert_textbox(fitz.Rect(box.x0 + 10, box.y0 + 26, box.x1 - 10, box.y1 - 4), NOTE,
                        fontname="helv", fontsize=10, color=(0.1, 0.35, 0.2))
    y = box.y1 + 16

    # 2단 구성
    col = (width - 20) / 2
    y_left = _para(page, LEFT_COL, m, y, col)
    y_right = _para(page, RIGHT_COL, m + col + 20, y, col)
    y = max(y_left, y_right) + 14

    # 그림 자리(도형) — 번역문이 이 위로 넘치면 안 된다
    chart = fitz.Rect(m + 60, y, 595 - m - 60, y + 110)
    page.draw_rect(chart, color=(0.5, 0.5, 0.5), width=0.6)
    for i, h in enumerate([40, 65, 90, 75]):
        x = chart.x0 + 30 + i * 70
        page.draw_rect(fitz.Rect(x, chart.y1 - 10 - h, x + 40, chart.y1 - 10), color=None, fill=(0.35, 0.6, 0.45))
    y = chart.y1 + 18

    y = _para(page, CLOSING, m, y, width)
    page.insert_text((595 / 2 - 3, 810), "1", fontname="helv", fontsize=9)
    doc.save(path)


def _para(page: fitz.Page, text: str, x: float, y: float, width: float, size: float = 10.5) -> float:
    """양쪽 정렬 문단을 넣고 아래쪽 y를 돌려준다."""
    rect = fitz.Rect(x, y, x + width, y + 400)
    left = page.insert_textbox(rect, text, fontname="helv", fontsize=size, lineheight=1.35, align=fitz.TEXT_ALIGN_JUSTIFY)
    return y + 400 - left


if __name__ == "__main__":
    build(sys.argv[1] if len(sys.argv) > 1 else "examples/sample_en.pdf")
