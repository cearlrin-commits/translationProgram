"""영어 PDF를 한국어로 번역해 원본과 같은 배치로 다시 그리고, 좌우로 비교하는 PDF를 만든다."""

from .memory import TranslationMemory
from .pipeline import Report, translate_pdf
from .translate import ArgosTranslator, ClaudeTranslator, DictionaryTranslator, GoogleTranslator, Translator

__all__ = ["translate_pdf", "Report", "Translator", "TranslationMemory", "GoogleTranslator", "ArgosTranslator",
           "DictionaryTranslator", "ClaudeTranslator"]
