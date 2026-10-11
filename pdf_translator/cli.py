"""명령줄 실행: python -m pdf_translator 입력.pdf -o 출력.pdf"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .memory import TranslationMemory
from .pipeline import translate_pdf
from .translate import ArgosTranslator, ClaudeTranslator, DictionaryTranslator, GoogleTranslator, TranslationError


def parse_pages(spec: str | None) -> list[int] | None:
    if not spec:
        return None
    pages: list[int] = []
    for part in spec.split(","):
        a, _, b = part.partition("-")
        pages.extend(range(int(a) - 1, int(b or a)))
    return pages


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="pdf_translator", description="영어 PDF → 한국어 번역 (왼쪽 원본 / 오른쪽 번역본)")
    ap.add_argument("input", help="번역할 영어 PDF")
    ap.add_argument("-o", "--output", help="좌우 비교 PDF 경로 (기본: 입력이름_ko.pdf)")
    ap.add_argument("--engine", choices=["google", "argos", "dict", "claude"], default="google",
                    help="google: 무료 Google 번역(기본값, API 키 불필요), argos: 내 컴퓨터에서 도는 무료 번역기(오프라인), "
                         "dict: 번역 사전만 사용, claude: Claude API(ANTHROPIC_API_KEY 필요)")
    ap.add_argument("--dict", dest="dict_path",
                    help="번역 사전 JSON 경로. 먼저 사전에서 찾고, 새로 번역해 검사를 통과한 문장을 추가한다 "
                         "(기본값: 입력 PDF와 같은 폴더의 translation_dict.json)")
    ap.add_argument("--no-dict", action="store_true", help="번역 사전을 읽지도 만들지도 않기")
    ap.add_argument("--model", default="claude-opus-5-5", help="Claude 모델 ID")
    ap.add_argument("--pages", help="번역할 쪽 (예: 1-3,5)")
    ap.add_argument("--translated-only", help="번역본만 담은 PDF도 저장할 경로")
    ap.add_argument("--report", help="검사/배치 보고서(JSON) 경로")
    ap.add_argument("--max-fix-iterations", type=int, default=6, help="배치 검증·수정 최대 반복 횟수")
    ap.add_argument("--no-header", action="store_true", help="좌우 비교 PDF 위쪽의 '원본/번역본' 머리글 빼기")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(message)s")
    src = Path(args.input)
    output = Path(args.output) if args.output else src.with_name(src.stem + "_ko.pdf")
    dict_path = Path(args.dict_path) if args.dict_path else src.with_name("translation_dict.json")
    memory = None if args.no_dict else TranslationMemory.load(dict_path)

    try:
        if args.engine == "claude":
            translator = ClaudeTranslator(model=args.model)
        elif args.engine == "argos":
            translator = ArgosTranslator()
        elif args.engine == "google":
            translator = GoogleTranslator()
        else:
            if memory is None or not len(memory):
                ap.error(f"--engine dict 에는 내용이 있는 번역 사전이 필요합니다: {dict_path}")
            translator = DictionaryTranslator(memory.entries)

        report = translate_pdf(src, output, translator, pages=parse_pages(args.pages),
                               translated_only_path=args.translated_only, report_path=args.report,
                               max_fix_iterations=args.max_fix_iterations, header=not args.no_header,
                               memory=memory)
    except TranslationError as exc:
        print(f"번역 실패: {exc}", file=sys.stderr)
        if memory is not None and memory.added:
            print(f"그때까지 번역한 {memory.added}문장은 번역 사전에 저장했습니다: {dict_path}", file=sys.stderr)
        return 2

    print(f"완료: {output}")
    if memory is not None:
        print(f"번역 사전: {dict_path} (전체 {report.dictionary_size}문장, 사전에서 찾음 {report.dictionary_hits}, "
              f"새로 추가 {report.dictionary_added})")
    for page in report.pages:
        issues = sum(len(p.translation_issues) for p in page.paragraphs)
        remaining = sum(len(p.remaining_layout_diffs) for p in page.paragraphs)
        adjusted = [p.id for p in page.paragraphs if p.adjustments]
        print(f"  {page.page}쪽: 문단 {len(page.paragraphs)}개, 배치 수정 {page.fix_iterations}회, "
              f"번역 문제 {issues}건, 남은 배치 차이 {remaining}건"
              + (f", 영역 맞춤 조정 문단: {', '.join(adjusted)}" if adjusted else ""))
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
