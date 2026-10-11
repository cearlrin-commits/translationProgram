"""명령줄 실행: python -m pdf_translator 입력.pdf -o 출력.pdf"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .pipeline import translate_pdf
from .translate import ClaudeTranslator, DictionaryTranslator, GoogleTranslator


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
    ap.add_argument("--engine", choices=["claude", "google", "dict"], default="claude",
                    help="claude: Claude API(ANTHROPIC_API_KEY 필요), google: 무료 Google 번역, dict: 번역 사전 JSON")
    ap.add_argument("--dict", dest="dict_path", help="--engine dict 일 때 사용할 {영어 문장: 한국어} JSON")
    ap.add_argument("--model", default="claude-opus-5-5", help="Claude 모델 ID")
    ap.add_argument("--pages", help="번역할 쪽 (예: 1-3,5)")
    ap.add_argument("--translated-only", help="번역본만 담은 PDF도 저장할 경로")
    ap.add_argument("--report", help="검사/배치 보고서(JSON) 경로")
    ap.add_argument("--max-fix-iterations", type=int, default=6, help="배치 검증·수정 최대 반복 횟수")
    ap.add_argument("--no-header", action="store_true", help="좌우 비교 PDF 위쪽의 '원본/번역본' 머리글 빼기")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(message)s")
    if args.engine == "claude":
        translator = ClaudeTranslator(model=args.model)
    elif args.engine == "google":
        translator = GoogleTranslator()
    else:
        if not args.dict_path:
            ap.error("--engine dict 에는 --dict 경로가 필요합니다.")
        translator = DictionaryTranslator.from_file(args.dict_path)

    src = Path(args.input)
    output = Path(args.output) if args.output else src.with_name(src.stem + "_ko.pdf")
    report = translate_pdf(src, output, translator, pages=parse_pages(args.pages),
                           translated_only_path=args.translated_only, report_path=args.report,
                           max_fix_iterations=args.max_fix_iterations, header=not args.no_header)

    print(f"완료: {output}")
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
