"""영어 → 한국어 번역 엔진과, 문장/문단 검사를 거치는 번역 단계."""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from pathlib import Path

from .checks import check_paragraph, check_sentence
from .memory import TranslationMemory, normalize
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
        try:
            out = self._engine.translate_batch(flat) if flat else []
        except Exception as exc:
            raise TranslationError(f"Google 번역에 실패했습니다(인터넷 연결 확인): {exc}") from exc
        results, i = [], 0
        for p in paragraphs:
            results.append([t or "" for t in out[i:i + len(p)]])
            i += len(p)
        return results


class ArgosTranslator(Translator):
    """Argos Translate: 내 컴퓨터에서 도는 무료 오픈소스 번역기(인터넷·API 키 불필요).

    처음 한 번만 영어→한국어 모델을 내려받는다. `pip install argostranslate` 필요.
    """

    name = "argos"

    def __init__(self):
        try:
            import argostranslate.package
            import argostranslate.translate
        except ImportError as exc:
            raise TranslationError("Argos 엔진을 쓰려면 먼저 `pip install argostranslate` 를 실행하세요.") from exc
        if not self._installed(argostranslate.translate):
            log.warning("Argos 영어→한국어 모델을 처음 한 번 내려받습니다...")
            argostranslate.package.update_package_index()
            if not argostranslate.package.install_package_for_language_pair("en", "ko"):
                raise TranslationError("Argos 영어→한국어 모델을 찾거나 내려받지 못했습니다.")
        self._translation = argostranslate.translate.get_translation_from_codes("en", "ko")

    @staticmethod
    def _installed(translate_module) -> bool:
        langs = {lang.code: lang for lang in translate_module.get_installed_languages()}
        return "en" in langs and "ko" in langs and langs["en"].get_translation(langs["ko"]) is not None

    def translate_paragraphs(self, paragraphs: list[list[str]], context: str = "") -> list[list[str]]:
        return [[self._translation.translate(s) for s in p] for p in paragraphs]


class DictionaryTranslator(Translator):
    """번역 사전(JSON: {"영어 문장": "한국어 문장"})만으로 번역한다. 사전에 없는 문장은 영어 그대로 남는다."""

    name = "dict"

    def __init__(self, mapping: dict[str, str]):
        self.mapping = {normalize(k): v for k, v in mapping.items()}

    @classmethod
    def from_file(cls, path: str | Path) -> "DictionaryTranslator":
        return cls(json.loads(Path(path).read_text(encoding="utf-8")))

    def translate_paragraphs(self, paragraphs: list[list[str]], context: str = "") -> list[list[str]]:
        return [[self.mapping.get(normalize(s), s) for s in p] for p in paragraphs]


def translate_document(pages: list[PageContent], translator: Translator, max_retries: int = 2,
                       memory: TranslationMemory | None = None) -> dict[str, TranslatedParagraph]:
    """문단 단위로 번역한 뒤 문단 검사 → 문장 검사 순으로 확인하고, 문제 있는 부분은 다시 번역한다.

    memory(번역 사전)가 있으면 사전에 있는 문장은 그대로 쓰고 없는 문장만 번역 엔진에 보낸다.
    검사를 통과한 새 번역은 사전에 추가하고 쪽마다 저장한다(중간에 멈춰도 그때까지 번역은 남는다).
    """
    results: dict[str, TranslatedParagraph] = {}
    for page in pages:
        paras = [p for p in page.paragraphs if p.translatable and p.sentences]
        if not paras:
            continue
        context = "\n".join(p.text for p in page.paragraphs)
        known = [[memory.get(s) if memory else None for s in p.sentences] for p in paras]
        targets = [list(k) for k in known]

        # 사전에 없는 문장만 문단 묶음을 유지해서 번역 엔진에 보낸다
        need = [i for i, k in enumerate(known) if any(v is None for v in k)]
        if need:
            log.info("페이지 %d: 문단 %d개 번역 중 (%s, 사전에서 찾은 문장 %d개)", page.number + 1, len(need),
                     translator.name, sum(v is not None for k in known for v in k))
            requests = [[s for s, v in zip(paras[i].sentences, known[i]) if v is None] for i in need]
            outputs = translator.translate_paragraphs(requests, context)
            for i, req, out in zip(need, requests, outputs):
                out = list(out)
                if len(req) == len(paras[i].sentences):
                    targets[i] = out  # 문장 수가 어긋나면 아래 문단 검사에서 다시 번역한다
                elif len(out) == len(req):
                    it = iter(out)
                    targets[i] = [t if t is not None else next(it) for t in targets[i]]
                else:
                    targets[i] = [t if t is not None else translator.translate_sentence(s, context)
                                  for s, t in zip(paras[i].sentences, targets[i])]
        else:
            log.info("페이지 %d: 모든 문장을 번역 사전에서 찾았습니다.", page.number + 1)

        for para, k, out in zip(paras, known, targets):
            locked = {j for j, v in enumerate(k) if v is not None}
            tp = _review(para.id, para.sentences, out, translator, context, max_retries, locked)
            tp.from_dictionary = len(locked)
            results[para.id] = tp
            if memory is not None:
                _remember(memory, tp, locked)
        if memory is not None:
            memory.save()
    return results


def _remember(memory: TranslationMemory, tp: TranslatedParagraph, locked: set[int]) -> None:
    """검사를 통과한 새 번역 문장을 사전에 추가한다."""
    if any(i.code in ("sentence_count_mismatch", "duplicate_sentence") for i in tp.issues):
        return  # 문장 짝이 맞는지 확신할 수 없으면 사전에 넣지 않는다
    bad = {i.sentence_index for i in tp.issues if i.level == "sentence"}
    for j, (src, tgt) in enumerate(zip(tp.source_sentences, tp.sentences)):
        if j not in locked and j not in bad:
            memory.add(src, tgt)


def _review(pid: str, sources: list[str], targets: list[str], translator: Translator, context: str,
            max_retries: int, locked: set[int] = frozenset()) -> TranslatedParagraph:
    """locked: 번역 사전에서 가져온 문장 번호. 사람이 확인한 번역으로 보고 다시 번역하지 않는다."""
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
        if i in locked:
            continue
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
