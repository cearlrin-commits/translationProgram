"""영어 → 한국어 번역 엔진과, 문장/문단 검사를 거치는 번역 단계."""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from pathlib import Path

from .checks import check_paragraph, check_sentence
from .models import PageContent, TranslatedParagraph

log = logging.getLogger(__name__)


class TranslationError(RuntimeError):
    pass


class Translator(ABC):
    """문단별 문장 목록을 받아 같은 모양의 한국어 문장 목록을 돌려준다."""

    name = "base"

    @abstractmethod
    def translate_paragraphs(self, paragraphs: list[list[str]], context: str = "") -> list[list[str]]:
        ...

    def translate_sentence(self, sentence: str, context: str = "") -> str:
        result = self.translate_paragraphs([[sentence]], context)
        return " ".join(result[0]) if result and result[0] else ""


SYSTEM_PROMPT = """\
You are a professional English-to-Korean translator for documents.
Translate each English sentence into natural, accurate Korean (formal written style, 합니다/한다 체 consistent with the source tone).
Rules:
- Keep exactly one Korean sentence for each English sentence, in the same order, so sentences stay aligned.
- Keep every number, unit, symbol, URL, code and formula exactly as written.
- Keep proper nouns, product names and well-known acronyms in their usual Korean form or in English if that is customary.
- Use consistent terminology across paragraphs, using the page context for meaning.
- Do not add explanations, notes or romanization."""


class ClaudeTranslator(Translator):
    """Anthropic Claude API 번역기 (ANTHROPIC_API_KEY 필요)."""

    name = "claude"

    def __init__(self, model: str = "claude-opus-5-5", effort: str = "medium", max_chars: int = 6000):
        import anthropic

        self.client = anthropic.Anthropic()
        self.model = model
        self.effort = effort
        self.max_chars = max_chars

    def translate_paragraphs(self, paragraphs: list[list[str]], context: str = "") -> list[list[str]]:
        results: list[list[str]] = []
        for chunk in self._chunks(paragraphs):
            results.extend(self._translate_chunk(chunk, context))
        return results

    def _chunks(self, paragraphs: list[list[str]]):
        chunk: list[list[str]] = []
        size = 0
        for p in paragraphs:
            n = sum(len(s) for s in p)
            if chunk and size + n > self.max_chars:
                yield chunk
                chunk, size = [], 0
            chunk.append(p)
            size += n
        if chunk:
            yield chunk

    def _translate_chunk(self, paragraphs: list[list[str]], context: str) -> list[list[str]]:
        payload = {"paragraphs": [{"id": str(i), "sentences": s} for i, s in enumerate(paragraphs)]}
        content = ""
        if context:
            content += f"<page_context>\n{context}\n</page_context>\n\n"
        content += (
            "Translate the sentences below into Korean. Return the same ids, and for each paragraph "
            "exactly as many sentences as given.\n\n" + json.dumps(payload, ensure_ascii=False)
        )
        schema = {
            "type": "object",
            "properties": {
                "paragraphs": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "sentences": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["id", "sentences"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["paragraphs"],
            "additionalProperties": False,
        }
        response = self.client.beta.messages.create(
            model=self.model,
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": content}],
            output_config={"effort": self.effort, "format": {"type": "json_schema", "schema": schema}},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        if response.stop_reason == "refusal":
            raise TranslationError("번역 요청이 거절되었습니다.")
        if response.stop_reason == "max_tokens":
            raise TranslationError("응답이 max_tokens에서 잘렸습니다. max_chars를 줄여 주세요.")
        text = next(b.text for b in response.content if b.type == "text")
        by_id = {p["id"]: p["sentences"] for p in json.loads(text)["paragraphs"]}
        return [by_id.get(str(i), []) for i in range(len(paragraphs))]


class GoogleTranslator(Translator):
    """무료 Google 번역(deep-translator, API 키 불필요). 문장 단위로 번역한다."""

    name = "google"

    def __init__(self):
        from deep_translator import GoogleTranslator as _Google

        self._engine = _Google(source="en", target="ko")

    def translate_paragraphs(self, paragraphs: list[list[str]], context: str = "") -> list[list[str]]:
        flat = [s for p in paragraphs for s in p]
        out = self._engine.translate_batch(flat) if flat else []
        results, i = [], 0
        for p in paragraphs:
            results.append([t or "" for t in out[i:i + len(p)]])
            i += len(p)
        return results


class DictionaryTranslator(Translator):
    """미리 준비한 번역(JSON: {"영어 문장": "한국어 문장"})을 사용한다. 수동 번역/검수용."""

    name = "dict"

    def __init__(self, mapping: dict[str, str]):
        self.mapping = {_norm(k): v for k, v in mapping.items()}

    @classmethod
    def from_file(cls, path: str | Path) -> "DictionaryTranslator":
        return cls(json.loads(Path(path).read_text(encoding="utf-8")))

    def translate_paragraphs(self, paragraphs: list[list[str]], context: str = "") -> list[list[str]]:
        return [[self.mapping.get(_norm(s), s) for s in p] for p in paragraphs]


def _norm(s: str) -> str:
    return " ".join(s.split())


def translate_document(pages: list[PageContent], translator: Translator, max_retries: int = 2) -> dict[str, TranslatedParagraph]:
    """문단 단위로 번역한 뒤 문단 검사 → 문장 검사 순으로 확인하고, 문제 있는 부분은 다시 번역한다."""
    results: dict[str, TranslatedParagraph] = {}
    for page in pages:
        paras = [p for p in page.paragraphs if p.translatable and p.sentences]
        if not paras:
            continue
        context = "\n".join(p.text for p in page.paragraphs)
        log.info("페이지 %d: 문단 %d개 번역 중 (%s)", page.number + 1, len(paras), translator.name)
        outputs = translator.translate_paragraphs([p.sentences for p in paras], context)
        for para, out in zip(paras, outputs):
            results[para.id] = _review(para.id, para.sentences, list(out), translator, context, max_retries)
    return results


def _review(pid: str, sources: list[str], targets: list[str], translator: Translator, context: str, max_retries: int) -> TranslatedParagraph:
    tp = TranslatedParagraph(pid, sources, targets)

    # 1) 문단 단위 검사: 문장 수가 어긋나면 문단을 다시 번역하고, 그래도 안 되면 문장별로 번역한다.
    for _ in range(max_retries):
        if not any(i.code == "sentence_count_mismatch" for i in check_paragraph(pid, sources, tp.sentences)):
            break
        tp.retries += 1
        tp.sentences = list(translator.translate_paragraphs([sources], context)[0])
    if len(tp.sentences) != len(sources):
        tp.retries += 1
        tp.sentences = [translator.translate_sentence(s, context) for s in sources]

    # 2) 문장 단위 검사: 문제가 있는 문장만 다시 번역한다.
    for i, src in enumerate(sources):
        for _ in range(max_retries):
            if not check_sentence(pid, i, src, tp.sentences[i]):
                break
            tp.retries += 1
            tp.sentences[i] = translator.translate_sentence(src, context)

    # 3) 최종 검사 결과(남은 문제)를 기록한다.
    tp.issues = [iss for i, src in enumerate(sources) for iss in check_sentence(pid, i, src, tp.sentences[i])]
    tp.issues += check_paragraph(pid, sources, tp.sentences)
    for issue in tp.issues:
        log.warning("[%s] %s 검사: %s", pid, issue.level, issue.message)
    return tp
