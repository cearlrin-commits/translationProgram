import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "examples"))

from pdf_translator.translate import DictionaryTranslator, Translator  # noqa: E402

SYLLABLES = "가나다라마바사아자차카타파하고노도로모보소오조호"


class FakeKoreanTranslator(Translator):
    """네트워크 없이 테스트하기 위한 가짜 번역기: 숫자는 유지하고 나머지는 원문 길이에 비례한 한글로 바꾼다."""

    name = "fake"

    def __init__(self, ratio: float = 0.6):
        self.ratio = ratio
        self.calls = 0

    def korean(self, sentence: str) -> str:
        out = []
        for tok in re.findall(r"\d+(?:[.,]\d+)*%?|[A-Za-z']+|[^\sA-Za-z\d]", sentence):
            if tok[0].isdigit():
                out.append(tok)
            elif tok[0].isalpha():
                n = max(1, round(len(tok) * self.ratio))
                out.append("".join(SYLLABLES[(len(tok) + i) % len(SYLLABLES)] for i in range(n)))
            elif out:
                out[-1] += tok
        return " ".join(out)

    def translate_paragraphs(self, paragraphs, context=""):
        self.calls += 1
        return [[self.korean(s) for s in p] for p in paragraphs]


@pytest.fixture(scope="session")
def sample_pdf(tmp_path_factory) -> Path:
    from make_sample import build

    path = tmp_path_factory.mktemp("pdf") / "sample_en.pdf"
    build(str(path))
    return path


@pytest.fixture
def fake_translator():
    return FakeKoreanTranslator()


@pytest.fixture
def dict_translator():
    return DictionaryTranslator(json.loads((ROOT / "examples" / "sample_translations_ko.json").read_text(encoding="utf-8")))
