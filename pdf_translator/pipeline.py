"""전체 흐름: 텍스트 인식 → 번역(문장/문단 검사) → 배치 → 배치 검증·수정 반복 → 좌우 비교 PDF."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pymupdf as fitz

from .extract import extract_document
from .fonts import KoreanFonts, default_fonts
from .layout import LayoutEngine, LayoutParams, Plan, compute_regions, erase_original_text
from .memory import TranslationMemory
from .models import PageContent, Paragraph, TranslatedParagraph
from .translate import Translator, translate_document
from .verify import LayoutDiff, verify_page

log = logging.getLogger(__name__)


@dataclass
class ParagraphReport:
    id: str
    source: str
    translation: str
    font_size: float
    rendered_size: float
    adjustments: list[str]
    translation_issues: list[dict]
    translation_retries: int
    from_dictionary: int
    remaining_layout_diffs: list[dict]
    accepted_diffs: list[dict]


@dataclass
class PageReport:
    page: int
    fix_iterations: int
    fixes: list[str] = field(default_factory=list)
    paragraphs: list[ParagraphReport] = field(default_factory=list)


@dataclass
class Report:
    input: str
    output: str
    translator: str
    dictionary: str | None = None  # 번역 사전 파일
    dictionary_hits: int = 0  # 사전에서 찾아 쓴 문장 수
    dictionary_added: int = 0  # 이번에 사전에 새로 추가된 문장 수
    dictionary_size: int = 0
    pages: list[PageReport] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(not p.remaining_layout_diffs and not p.translation_issues
                   for page in self.pages for p in page.paragraphs)

    def to_json(self) -> str:
        return json.dumps(asdict(self) | {"ok": self.ok}, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# 배치 + 검증 + 수정 반복
# ---------------------------------------------------------------------------

def render_translated_page(src: fitz.Document, content: PageContent, translations: dict[str, TranslatedParagraph],
                           params: dict[str, LayoutParams], engine: LayoutEngine, regions) -> tuple[fitz.Document, dict[str, Plan]]:
    """원본 페이지를 복사해 원문 글자를 지우고 한국어를 그린 1쪽짜리 문서를 만든다."""
    out = fitz.open()
    out.insert_pdf(src, from_page=content.number, to_page=content.number)
    page = out[0]
    targets = [p for p in content.paragraphs if p.id in translations]
    erase_original_text(page, targets, params)
    plans: dict[str, Plan] = {}
    for para in targets:
        plan = engine.plan(para, translations[para.id].text, regions[para.id], params[para.id])
        engine.render(page, plan, params[para.id])
        plans[para.id] = plan
    # 실제 저장된 결과를 다시 읽어 검증하도록 직렬화 후 다시 연다
    return fitz.open("pdf", out.tobytes(garbage=3, deflate=True)), plans


def apply_fixes(diffs: list[LayoutDiff], params: dict[str, LayoutParams], plans: dict[str, Plan],
                paras: dict[str, Paragraph], accepted: dict[str, list[LayoutDiff]]) -> list[str]:
    """검증에서 발견된 차이를 원본에 맞추도록 배치 설정을 고친다. 고친 내용 목록을 돌려준다."""
    fixes: list[str] = []
    moved = {d.paragraph_id for d in diffs if d.kind in ("position_x", "position_y", "font_size")}
    for d in diffs:
        if not d.is_hard or any(a.kind == d.kind for a in accepted.get(d.paragraph_id, [])):
            continue
        if d.kind in ("overflow", "overflow_x", "overlap") and d.paragraph_id in moved:
            continue  # 위치/크기를 먼저 바로잡은 뒤 다시 검사한다
        p, plan, para = params[d.paragraph_id], plans[d.paragraph_id], paras[d.paragraph_id]
        shrunk = plan.font_size < para.font_size - 0.01
        if d.kind == "font_size":
            if p.size_override is not None:
                p.size_override = None
                fixes.append(f"{d.paragraph_id}: 지정된 글자 크기 해제 → 원본 크기 {para.font_size:.2f}pt로 복원")
            elif shrunk:
                accepted.setdefault(d.paragraph_id, []).append(d)
                fixes.append(f"{d.paragraph_id}: 원본 크기로는 영역에 들어가지 않아 축소 유지({plan.font_size:.2f}pt)")
            else:
                p.size_correction *= para.font_size / float(d.actual)
                fixes.append(f"{d.paragraph_id}: 글자 크기 보정 ×{p.size_correction:.3f}")
        elif d.kind == "position_x":
            p.dx -= float(d.actual) - float(d.expected)
            fixes.append(f"{d.paragraph_id}: 가로 위치 {float(d.expected) - float(d.actual):+.2f}pt 이동")
        elif d.kind == "position_y":
            p.dy -= float(d.actual) - float(d.expected)
            fixes.append(f"{d.paragraph_id}: 세로 위치 {float(d.expected) - float(d.actual):+.2f}pt 이동")
        elif d.kind == "line_pitch":
            if any("줄 간격 축소" in a for a in plan.adjustments) or shrunk:
                accepted.setdefault(d.paragraph_id, []).append(d)
                fixes.append(f"{d.paragraph_id}: 영역에 맞추려고 줄 간격 축소 유지")
            else:
                p.pitch_correction *= float(d.expected) / float(d.actual)
                fixes.append(f"{d.paragraph_id}: 줄 간격 보정 ×{p.pitch_correction:.3f}")
        elif d.kind in ("overflow", "overlap"):
            if p.allow_extend and any("빈 공간" in a for a in plan.adjustments):
                p.allow_extend = False
                fixes.append(f"{d.paragraph_id}: 빈 공간 사용 중지(넘침/겹침)")
            elif p.bottom_shrink < para.font_size * 3:
                p.bottom_shrink += para.font_size * 0.5
                fixes.append(f"{d.paragraph_id}: 허용 영역을 줄여 다시 맞춤")
            else:
                accepted.setdefault(d.paragraph_id, []).append(d)
        elif d.kind == "overflow_x":
            accepted.setdefault(d.paragraph_id, []).append(d)  # 줄보다 긴 끊을 수 없는 단어 등
        elif d.kind == "original_text_left":
            if p.redact_pad < 2:
                p.redact_pad += 0.5
                fixes.append(f"{d.paragraph_id}: 원문 지우기 영역 확대")
            else:
                accepted.setdefault(d.paragraph_id, []).append(d)
        else:
            # color / missing_text 등 배치 설정으로 고칠 수 없는 차이는 보고서에 남긴다
            accepted.setdefault(d.paragraph_id, []).append(d)
    return fixes


def layout_page(src: fitz.Document, content: PageContent, translations: dict[str, TranslatedParagraph],
                engine: LayoutEngine, max_iterations: int = 6,
                initial_params: dict[str, LayoutParams] | None = None) -> tuple[fitz.Document, PageReport, list[LayoutDiff], dict[str, Plan], dict]:
    regions = compute_regions(content)
    page_tr = {p.id: translations[p.id] for p in content.paragraphs if p.id in translations}
    params = {pid: LayoutParams() for pid in page_tr}
    if initial_params:
        params.update(initial_params)
    paras = {p.id: p for p in content.paragraphs}
    accepted: dict[str, list[LayoutDiff]] = {}
    report = PageReport(content.number + 1, 0)

    doc, plans = render_translated_page(src, content, page_tr, params, engine, regions)
    diffs = verify_page(doc[0], content, page_tr, regions)
    for it in range(1, max_iterations + 1):
        pending = [d for d in diffs if d.is_hard
                   and not any(a.kind == d.kind for a in accepted.get(d.paragraph_id, []))]
        if not pending:
            break
        report.fix_iterations = it
        fixes = apply_fixes(pending, params, plans, paras, accepted)
        report.fixes.extend(f"[{it}회차] {f}" for f in fixes)
        for f in fixes:
            log.info("페이지 %d 배치 수정 %d회차: %s", content.number + 1, it, f)
        doc, plans = render_translated_page(src, content, page_tr, params, engine, regions)
        diffs = verify_page(doc[0], content, page_tr, regions)
    return doc, report, diffs, plans, accepted


# ---------------------------------------------------------------------------
# 좌우 비교 PDF
# ---------------------------------------------------------------------------

def compose_side_by_side(src: fitz.Document, translated: fitz.Document, pages: list[int],
                         gap: float = 24, header: bool = True) -> fitz.Document:
    """왼쪽에 영어 원본, 오른쪽에 한국어 번역본을 놓은 PDF를 만든다."""
    out = fitz.open()
    head = 22 if header else 0
    fonts = default_fonts()
    for i, n in enumerate(pages):
        rect = src[n].rect
        w, h = rect.width, rect.height
        page = out.new_page(width=w * 2 + gap, height=h + head)
        page.show_pdf_page(fitz.Rect(0, head, w, head + h), src, n)
        page.show_pdf_page(fitz.Rect(w + gap, head, 2 * w + gap, head + h), translated, i)
        page.draw_line((w + gap / 2, 4), (w + gap / 2, h + head - 4), color=(0.6, 0.6, 0.6), width=0.5)
        if header:
            tw = fitz.TextWriter(page.rect)
            tw.append((8, 15), f"원본 (English) · {n + 1}쪽", font=fonts.regular, fontsize=9)
            tw.append((w + gap + 8, 15), f"번역본 (한국어) · {n + 1}쪽", font=fonts.regular, fontsize=9)
            tw.write_text(page, color=(0.35, 0.35, 0.35))
    return out


# ---------------------------------------------------------------------------
# 진입점
# ---------------------------------------------------------------------------

def translate_pdf(input_path: str | Path, output_path: str | Path, translator: Translator, *,
                  pages: list[int] | None = None, translated_only_path: str | Path | None = None,
                  report_path: str | Path | None = None, max_fix_iterations: int = 6,
                  fonts: KoreanFonts | None = None, header: bool = True,
                  memory: TranslationMemory | None = None) -> Report:
    src = fitz.open(str(input_path))
    page_numbers = pages if pages is not None else list(range(len(src)))
    contents = extract_document(src, page_numbers)
    log.info("텍스트 인식 완료: %d쪽, 문단 %d개", len(contents), sum(len(c.paragraphs) for c in contents))

    translations = translate_document(contents, translator, memory=memory)
    engine = LayoutEngine(fonts or default_fonts())
    report = Report(str(input_path), str(output_path), translator.name)
    if memory is not None:
        report.dictionary = str(memory.path) if memory.path else None
        report.dictionary_hits, report.dictionary_added, report.dictionary_size = memory.hits, memory.added, len(memory)
    translated = fitz.open()
    for content in contents:
        doc, page_report, diffs, plans, accepted = layout_page(src, content, translations, engine, max_fix_iterations)
        translated.insert_pdf(doc)
        for para in content.paragraphs:
            if para.id not in translations:
                continue
            tp, plan = translations[para.id], plans[para.id]
            page_report.paragraphs.append(ParagraphReport(
                id=para.id, source=para.text, translation=tp.text, font_size=para.font_size,
                rendered_size=plan.font_size, adjustments=plan.adjustments,
                translation_issues=[asdict(i) for i in tp.issues], translation_retries=tp.retries, from_dictionary=tp.from_dictionary,
                remaining_layout_diffs=[asdict(d) for d in diffs if d.paragraph_id == para.id and d.is_hard
                                        and not any(a.kind == d.kind for a in accepted.get(para.id, []))],
                accepted_diffs=[asdict(d) for d in diffs if d.paragraph_id == para.id
                                and (not d.is_hard or any(a.kind == d.kind for a in accepted.get(para.id, [])))],
            ))
        report.pages.append(page_report)

    side = compose_side_by_side(src, translated, page_numbers, header=header)
    _save(side, output_path)
    if translated_only_path:
        _save(translated, translated_only_path)
    if report_path:
        Path(report_path).write_text(report.to_json(), encoding="utf-8")
    return report


def _save(doc: fitz.Document, path: str | Path) -> None:
    """사용한 글자만 남기도록 글꼴을 줄여(subset) 저장한다."""
    try:
        doc.subset_fonts()
    except Exception as exc:  # 글꼴 줄이기에 실패해도 PDF는 저장한다
        log.warning("글꼴 subset 실패: %s", exc)
    doc.save(str(path), garbage=3, deflate=True)
