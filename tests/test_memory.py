"""번역 사전: 번역하면서 만들어지고, 다음 번역에서 다시 쓰이는지 확인한다."""

import json

import pymupdf as fitz
import pytest

from pdf_translator import cli
from pdf_translator.extract import extract_document
from pdf_translator.memory import TranslationMemory
from pdf_translator.translate import ArgosTranslator, TranslationError, Translator, translate_document


class Counting(Translator):
    """몇 문장을 번역 엔진에 보냈는지 센다."""

    name = "counting"

    def __init__(self, inner):
        self.inner = inner
        self.sentences_sent = 0

    def translate_paragraphs(self, paragraphs, context=""):
        self.sentences_sent += sum(len(p) for p in paragraphs)
        return self.inner.translate_paragraphs(paragraphs, context)


def _pages(pdf):
    return extract_document(fitz.open(pdf))


def test_dictionary_is_built_while_translating(sample_pdf, fake_translator, tmp_path):
    path = tmp_path / "dict.json"
    memory = TranslationMemory.load(path)
    assert len(memory) == 0 and not path.exists()

    pages = _pages(sample_pdf)
    result = translate_document(pages, fake_translator, memory=memory)

    saved = json.loads(path.read_text(encoding="utf-8"))
    sources = [s for p in pages[0].paragraphs if p.translatable for s in p.sentences]
    assert set(saved) == set(sources)  # 검사를 통과한 모든 문장이 사전에 들어간다
    for tp in result.values():
        for src, tgt in zip(tp.source_sentences, tp.sentences):
            assert saved[src] == tgt
    assert memory.added == len(saved)


def test_second_run_uses_dictionary_instead_of_engine(sample_pdf, fake_translator, tmp_path):
    path = tmp_path / "dict.json"
    translate_document(_pages(sample_pdf), fake_translator, memory=TranslationMemory.load(path))

    counting = Counting(fake_translator)
    memory = TranslationMemory.load(path)
    result = translate_document(_pages(sample_pdf), counting, memory=memory)
    assert counting.sentences_sent == 0  # 번역 엔진을 전혀 부르지 않는다
    assert memory.added == 0 and memory.hits == len(memory)
    assert all(tp.from_dictionary == len(tp.sentences) and not tp.issues for tp in result.values())


def test_only_unknown_sentences_go_to_engine_and_edits_are_kept(sample_pdf, fake_translator, tmp_path):
    pages = _pages(sample_pdf)
    body = next(p for p in pages[0].paragraphs if p.text.startswith("Water was"))
    path = tmp_path / "dict.json"
    # 사람이 직접 넣은(고친) 번역 한 문장만 있는 사전
    path.write_text(json.dumps({body.sentences[0]: "물이 가장 큰 현실적 문제였다."}, ensure_ascii=False), encoding="utf-8")

    counting = Counting(fake_translator)
    memory = TranslationMemory.load(path)
    result = translate_document(pages, counting, memory=memory)

    total = sum(len(p.sentences) for p in pages[0].paragraphs if p.translatable)
    assert counting.sentences_sent == total - 1
    tp = result[body.id]
    assert tp.sentences[0] == "물이 가장 큰 현실적 문제였다." and tp.from_dictionary == 1
    assert len(tp.sentences) == len(body.sentences)  # 사전 문장과 새 번역이 제자리에 합쳐진다
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved[body.sentences[0]] == "물이 가장 큰 현실적 문제였다."  # 덮어쓰지 않음
    assert len(saved) == total


def test_failed_translations_are_not_added(sample_pdf, tmp_path):
    class Lazy(Translator):
        name = "lazy"

        def translate_paragraphs(self, paragraphs, context=""):
            return [list(p) for p in paragraphs]  # 영어 그대로

    path = tmp_path / "dict.json"
    memory = TranslationMemory.load(path)
    translate_document(_pages(sample_pdf), Lazy(), max_retries=1, memory=memory)
    assert memory.added == 0
    assert json.loads(path.read_text(encoding="utf-8")) == {}


def test_progress_is_saved_when_engine_fails_midway(fake_translator, tmp_path):
    pdf = tmp_path / "two_pages.pdf"
    doc = fitz.open()
    for text in ("The first page talks about rain barrels.", "The second page talks about shared tools."):
        doc.new_page().insert_text((72, 100), text, fontsize=11)
    doc.save(pdf)

    class FailsOnSecondPage(Translator):
        name = "fails"
        calls = 0

        def translate_paragraphs(self, paragraphs, context=""):
            self.calls += 1
            if self.calls > 1:
                raise TranslationError("network down")
            return fake_translator.translate_paragraphs(paragraphs, context)

    path = tmp_path / "dict.json"
    with pytest.raises(TranslationError):
        translate_document(_pages(pdf), FailsOnSecondPage(), memory=TranslationMemory.load(path))
    assert list(json.loads(path.read_text(encoding="utf-8"))) == ["The first page talks about rain barrels."]


def test_cli_builds_dictionary_next_to_input(sample_pdf, fake_translator, tmp_path, monkeypatch):
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(sample_pdf.read_bytes())
    monkeypatch.setattr(cli, "GoogleTranslator", lambda: fake_translator)  # 기본 엔진(google)을 오프라인 가짜로

    assert cli.main([str(pdf), "-o", str(tmp_path / "out.pdf")]) == 0
    dict_path = tmp_path / "translation_dict.json"
    built = json.loads(dict_path.read_text(encoding="utf-8"))
    assert built and (tmp_path / "out.pdf").exists()

    # 사전만으로 다시 번역(네트워크 없음)
    report_path = tmp_path / "report.json"
    assert cli.main([str(pdf), "-o", str(tmp_path / "out2.pdf"), "--engine", "dict", "--report", str(report_path)]) == 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["dictionary_hits"] == len(built) and report["dictionary_added"] == 0


def test_argos_engine_explains_missing_package():
    try:
        import argostranslate  # noqa: F401
    except ImportError:
        with pytest.raises(TranslationError, match="pip install argostranslate"):
            ArgosTranslator()
    else:
        pytest.skip("argostranslate가 설치되어 있음")
