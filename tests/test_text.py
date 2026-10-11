"""텍스트 인식, 문장 분리, 번역 검사(문장/문단)와 재번역."""

import pymupdf as fitz

from pdf_translator.checks import check_paragraph, check_sentence
from pdf_translator.extract import extract_document
from pdf_translator.segment import split_sentences
from pdf_translator.translate import Translator, translate_document


def test_split_sentences_handles_abbreviations_numbers_and_initials():
    text = ("Dr. Kim measured 3.5 liters, e.g. in Fig. 2 of the report. "
            "J. Smith agreed! Did it work? 1. Results follow.")
    assert split_sentences(text) == [
        "Dr. Kim measured 3.5 liters, e.g. in Fig. 2 of the report.",
        "J. Smith agreed!",
        "Did it work?",
        "1. Results follow.",
    ]


def test_extract_recognizes_paragraph_styles(sample_pdf):
    page = extract_document(fitz.open(sample_pdf))[0]
    by_text = {p.text: p for p in page.paragraphs}
    title = by_text["Community Gardens in Small Cities"]
    assert title.bold and title.font_size == 20 and title.align == "center"
    heading = by_text["1. What the gardeners told us"]
    assert heading.sentences == ["1. What the gardeners told us"]
    assert abs(heading.color[2] - 0.6) < 0.01
    body = [p for p in page.paragraphs if p.text.startswith("Water was")][0]
    assert body.align == "justify" and body.font_size == 10.5 and len(body.sentences) == 3
    # 두 단(column)은 서로 다른 문단으로 인식
    cols = [p for p in page.paragraphs if p.text.startswith(("Small cities", "The main risk"))]
    assert len(cols) == 2 and cols[0].bbox[2] < cols[1].bbox[0]
    # 숫자뿐인 쪽 번호는 번역 대상이 아니다
    assert not by_text["1"].translatable


def test_sentence_check_detects_problems():
    codes = lambda src, tgt: [i.code for i in check_sentence("p", 0, src, tgt)]  # noqa: E731
    assert codes("The garden has 18 plots.", "텃밭에는 18개의 구획이 있다.") == []
    assert codes("The garden has 18 plots.", "The garden has 18 plots.") == ["untranslated"]
    assert codes("The garden has 18 plots.", "") == ["empty"]
    assert codes("The garden has 18 plots.", "텃밭에는 많은 구획이 있다.") == ["number_mismatch"]
    assert "too_long" in codes("It rained.", "비가 왔다. 그리고 텃밭 참여자들은 모두 집으로 돌아가서 다음 날 다시 모이기로 했다.")
    assert codes("NASA", "NASA") == []  # 약어만 있는 문장은 그대로 두어도 된다


def test_paragraph_check_detects_alignment_problems():
    src = ["First sentence here.", "Second sentence here."]
    assert [i.code for i in check_paragraph("p", src, ["첫 번째 문장이다."])] == ["sentence_count_mismatch"]
    assert [i.code for i in check_paragraph("p", src, ["같은 문장이다.", "같은 문장이다."])] == ["duplicate_sentence"]
    assert check_paragraph("p", src, ["첫 번째 문장이다.", "두 번째 문장이다."]) == []


class FlakyTranslator(Translator):
    """처음에는 문장을 합치거나 영어로 남기고, 다시 요청하면 제대로 번역하는 번역기."""

    name = "flaky"

    def __init__(self, good):
        self.good = good
        self.paragraph_calls = 0
        self.sentence_calls = 0

    def translate_paragraphs(self, paragraphs, context=""):
        self.paragraph_calls += 1
        if self.paragraph_calls == 1:
            out = []
            for p in paragraphs:
                if len(p) >= 3:
                    out.append([self.good.korean(p[0] + " " + p[1])] + [self.good.korean(s) for s in p[2:]])  # 문장 합침
                else:
                    out.append([s for s in p])  # 영어 그대로
            return out
        return [[self.good.korean(s) for s in p] for p in paragraphs]

    def translate_sentence(self, sentence, context=""):
        self.sentence_calls += 1
        return self.good.korean(sentence)


def test_translation_is_rechecked_and_retried(sample_pdf, fake_translator):
    pages = extract_document(fitz.open(sample_pdf))
    flaky = FlakyTranslator(fake_translator)
    result = translate_document(pages, flaky)
    paras = [p for p in pages[0].paragraphs if p.translatable]
    assert set(result) == {p.id for p in paras}
    for p in paras:
        tp = result[p.id]
        assert len(tp.sentences) == len(p.sentences)  # 문장 정렬 유지
        assert tp.issues == [], (p.id, tp.issues)
    assert any(tp.retries for tp in result.values())
    assert flaky.paragraph_calls > 1  # 문단 단위 재번역
    assert flaky.sentence_calls > 0  # 문장 단위 재번역


def test_remaining_problems_are_reported(sample_pdf):
    class Lazy(Translator):
        name = "lazy"

        def translate_paragraphs(self, paragraphs, context=""):
            return [list(p) for p in paragraphs]  # 끝까지 번역하지 않음

    result = translate_document(extract_document(fitz.open(sample_pdf)), Lazy(), max_retries=1)
    codes = {i.code for tp in result.values() for i in tp.issues}
    assert "untranslated" in codes
