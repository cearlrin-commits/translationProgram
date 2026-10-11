"""한국어 글꼴 관리.

기본 글꼴은 나눔고딕(NanumGothic, SIL Open Font License 1.1)으로, 무료로 사용·배포할 수 있다.
나눔고딕에 없는 글자(그리스 문자 등)는 PyMuPDF에 내장된 Droid Sans Fallback(Apache 2.0)으로 그린다.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import pymupdf as fitz

FONT_DIR = Path(__file__).parent / "fonts"
REGULAR = FONT_DIR / "NanumGothic-Regular.ttf"
BOLD = FONT_DIR / "NanumGothic-Bold.ttf"
FONT_NAMES = ("NanumGothic", "DroidSansFallback", "Droid Sans Fallback")


class KoreanFonts:
    def __init__(self, regular: Path = REGULAR, bold: Path = BOLD):
        self.regular = fitz.Font(fontfile=str(regular))
        self.bold = fitz.Font(fontfile=str(bold))
        self.fallback = fitz.Font("cjk")

    def main(self, bold: bool) -> fitz.Font:
        return self.bold if bold else self.regular

    def runs(self, text: str, bold: bool) -> list[tuple[fitz.Font, str]]:
        """글꼴에 없는 글자는 대체 글꼴로 나누어 (글꼴, 문자열) 조각 목록을 만든다."""
        main = self.main(bold)
        runs: list[tuple[fitz.Font, str]] = []
        for ch in text:
            font = main if (ch == " " or main.has_glyph(ord(ch))) else self.fallback
            if runs and runs[-1][0] is font:
                runs[-1] = (font, runs[-1][1] + ch)
            else:
                runs.append((font, ch))
        return runs

    def width(self, text: str, size: float, bold: bool) -> float:
        return sum(f.text_length(t, fontsize=size) for f, t in self.runs(text, bold))

    def descent(self, bold: bool) -> float:
        return abs(self.main(bold).descender)


@lru_cache(maxsize=1)
def default_fonts() -> KoreanFonts:
    return KoreanFonts()
