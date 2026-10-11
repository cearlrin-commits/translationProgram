"""영어 문단을 문장 단위로 나눈다."""

from __future__ import annotations

import re

# 마침표가 붙어도 문장이 끝나지 않는 약어 (소문자, 끝의 마침표 제외)
ABBREVIATIONS = {
    "mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "mt", "vs", "etc",
    "e.g", "i.e", "cf", "al", "fig", "figs", "eq", "eqs", "no", "nos", "vol",
    "vols", "pp", "p", "ch", "sec", "inc", "ltd", "co", "corp", "dept", "approx",
    "est", "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct",
    "nov", "dec", "u.s", "u.k", "ph.d", "ref", "refs", "ed", "eds", "op", "gen",
}

# 문장부호 + (닫는 따옴표/괄호) + 공백 + 다음 문장 시작(대문자, 숫자, 여는 따옴표/괄호)
_BOUNDARY = re.compile(r"([.!?]+[\"'”’)\]]*)\s+(?=[\"'“‘(\[]?[A-Z0-9])")


def split_sentences(text: str) -> list[str]:
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []
    sentences: list[str] = []
    start = 0
    for m in _BOUNDARY.finditer(text):
        end = m.end(1)
        candidate = text[start:end]
        if _ends_with_abbreviation(candidate):
            continue
        sentences.append(candidate.strip())
        start = m.end()
    tail = text[start:].strip()
    if tail:
        sentences.append(tail)
    return sentences


def _ends_with_abbreviation(chunk: str) -> bool:
    if not chunk.endswith("."):
        return False
    if re.fullmatch(r"\s*\(?(\d+(\.\d+)*|[IVXivx]+)[.)]", chunk):  # "1." "2.3." "IV." 같은 번호
        return True
    words = chunk[:-1].split()
    if not words:
        return False
    last = words[-1].lstrip("\"'“‘([").lower()
    if last in ABBREVIATIONS:
        return True
    # 이니셜 (J. Smith, U. S.)
    return len(last) == 1 and last.isalpha()
