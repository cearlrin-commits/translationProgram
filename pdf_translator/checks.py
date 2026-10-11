"""번역 결과 검사: 문장 단위 검사와 문단 단위 검사."""

from __future__ import annotations

import re

from .models import TranslationIssue

_HANGUL = re.compile(r"[가-힣]")
_LATIN = re.compile(r"[A-Za-z]")
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
_WORD = re.compile(r"[A-Za-z]{2,}")


def _numbers(text: str) -> list[str]:
    return sorted(n.replace(",", "") for n in _NUMBER.findall(text))


def _needs_korean(source: str) -> bool:
    """고유명사·약어·코드만 있는 문장이 아니라 실제 번역이 필요한 문장인지."""
    words = [w for w in _WORD.findall(source) if not w.isupper()]
    return len(words) >= 2


def _compact_len(text: str) -> int:
    return len(re.sub(r"\s", "", text))


def check_sentence(paragraph_id: str, index: int, source: str, target: str) -> list[TranslationIssue]:
    issues: list[TranslationIssue] = []

    def add(code: str, message: str) -> None:
        issues.append(TranslationIssue("sentence", paragraph_id, code, message, index))

    target = (target or "").strip()
    if not target:
        add("empty", "번역문이 비어 있습니다.")
        return issues
    if _needs_korean(source):
        if target == source.strip() or not _HANGUL.search(target):
            add("untranslated", "번역되지 않았습니다(한글 없음).")
            return issues
        latin = len(_LATIN.findall(target))
        hangul = len(_HANGUL.findall(target))
        if latin > hangul * 1.5:
            add("english_leftover", "번역문에 영어가 지나치게 많이 남아 있습니다.")
    src_nums, tgt_nums = _numbers(source), _numbers(target)
    missing = [n for n in src_nums if n not in tgt_nums]
    if missing:
        add("number_mismatch", f"원문의 숫자가 번역문에 없습니다: {', '.join(missing)}")
    if _needs_korean(source):
        ratio = _compact_len(target) / max(1, _compact_len(source))
        if ratio < 0.12:
            add("too_short", f"원문에 비해 번역문이 너무 짧습니다(비율 {ratio:.2f}). 누락 의심.")
        elif ratio > 2.0:
            add("too_long", f"원문에 비해 번역문이 너무 깁니다(비율 {ratio:.2f}). 다른 문장이 섞였을 수 있습니다.")
    return issues


def check_paragraph(paragraph_id: str, sources: list[str], targets: list[str]) -> list[TranslationIssue]:
    issues: list[TranslationIssue] = []

    def add(code: str, message: str) -> None:
        issues.append(TranslationIssue("paragraph", paragraph_id, code, message))

    if len(sources) != len(targets):
        add("sentence_count_mismatch", f"문장 수가 다릅니다(원문 {len(sources)}, 번역 {len(targets)}).")
        return issues
    src_text, tgt_text = " ".join(sources), " ".join(targets)
    missing = [n for n in _numbers(src_text) if n not in _numbers(tgt_text)]
    if missing:
        add("number_mismatch", f"문단의 숫자가 번역문에 없습니다: {', '.join(missing)}")
    for i in range(1, len(targets)):
        if targets[i].strip() and targets[i].strip() == targets[i - 1].strip() and sources[i] != sources[i - 1]:
            add("duplicate_sentence", f"{i}번째와 {i + 1}번째 문장의 번역이 같습니다(문장 정렬 어긋남 의심).")
    if _needs_korean(src_text):
        ratio = _compact_len(tgt_text) / max(1, _compact_len(src_text))
        if ratio < 0.15 or ratio > 1.8:
            add("length_ratio", f"문단 길이 비율이 비정상적입니다({ratio:.2f}).")
    return issues
