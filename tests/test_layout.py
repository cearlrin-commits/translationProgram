"""배치 테스트: 번역본의 글자 크기·위치를 원본과 비교하고, 다르면 원본에 맞게 고쳐지는지 확인한다."""

import json
import statistics

import pymupdf as fitz

from pdf_translator.extract import extract_document
from pdf_translator.fonts import default_fonts
from pdf_translator.layout import LayoutEngine, LayoutParams, compute_regions
from pdf_translator.pipeline import layout_page, translate_pdf
from pdf_translator.translate import translate_document
from pdf_translator.verify import read_rendered, verify_page


def _setup(sample_pdf, translator):
    src = fitz.open(sample_pdf)
    content = extract_document(src)[0]
    translations = translate_document([content], translator)
    return src, content, translations, LayoutEngine(default_fonts())


def _assert_matches_original(doc, content, translations):
    """번역본을 다시 읽어 각 문단의 글자 크기·첫 줄 위치·색이 원본과 같은지 직접 비교한다."""
    ours, others = read_rendered(doc[0])
    for p in content.paragraphs:
        if p.id not in translations:
            continue
        x0, y0, x1, y1 = p.bbox
        items = [i for i in ours if abs(i.origin[1] - p.first_baseline) < 0.6 and x0 - 30 <= i.bbox[0] <= x1]
        assert items, f"{p.id}: 첫 줄을 찾지 못함"
        assert abs(statistics.median(i.size for i in items) - p.font_size) <= 0.25, p.id
        if p.align in ("left", "justify"):
            assert abs(min(i.bbox[0] for i in items) - p.lines[0].bbox[0]) <= 1.0, p.id
        if p.align == "center":
            c = (min(i.bbox[0] for i in items) + max(i.bbox[2] for i in items)) / 2
            assert abs(c - (p.lines[0].bbox[0] + p.lines[0].bbox[2]) / 2) <= 1.0, p.id
        assert all(max(abs(a - b) for a, b in zip(i.color, p.color)) < 0.03 for i in items), p.id
        # 원문 글자는 모두 지워졌다
        assert not [o for o in others if o.bbox[1] >= y0 - 1 and o.bbox[3] <= y1 + 1 and o.bbox[0] >= x0 - 1 and o.bbox[2] <= x1 + 1]


def test_layout_matches_original(sample_pdf, dict_translator):
    src, content, translations, engine = _setup(sample_pdf, dict_translator)
    doc, report, diffs, plans, accepted = layout_page(src, content, translations, engine)
    assert [d for d in diffs if d.is_hard] == []
    assert all(plan.font_size == next(p.font_size for p in content.paragraphs if p.id == pid) for pid, plan in plans.items())
    _assert_matches_original(doc, content, translations)
    # 배경 상자와 그림(도형)은 번역본에 그대로 남아 있다
    assert len(doc[0].get_drawings()) == len(src[0].get_drawings())


def test_fix_loop_corrects_wrong_size_and_position(sample_pdf, dict_translator):
    """일부러 글자 크기와 위치를 틀리게 배치해도 검증 → 수정 반복으로 원본과 같아진다."""
    src, content, translations, engine = _setup(sample_pdf, dict_translator)
    ids = [p.id for p in content.paragraphs if p.id in translations]
    broken = {
        ids[0]: LayoutParams(size_override=14.0),  # 제목 20pt → 14pt
        ids[2]: LayoutParams(dx=7.5, dy=-4.0),  # 본문 위치 어긋남
        ids[4]: LayoutParams(size_override=9.0, dx=-3.0),  # 소제목 13pt → 9pt, 위치 어긋남
        ids[6]: LayoutParams(pitch_correction=1.3),  # 줄 간격 어긋남
    }

    regions = compute_regions(content)
    from pdf_translator.pipeline import render_translated_page
    params = {pid: LayoutParams() for pid in ids} | {k: LayoutParams(**vars(v)) for k, v in broken.items()}
    before, _ = render_translated_page(src, content, translations, params, engine, regions)
    kinds = {(d.paragraph_id, d.kind) for d in verify_page(before[0], content, translations, regions)}
    assert {(ids[0], "font_size"), (ids[2], "position_x"), (ids[2], "position_y"),
            (ids[4], "font_size"), (ids[6], "line_pitch")} <= kinds

    doc, report, diffs, plans, accepted = layout_page(src, content, translations, engine, initial_params=broken)
    assert report.fix_iterations >= 1 and report.fixes
    assert [d for d in diffs if d.is_hard] == []
    _assert_matches_original(doc, content, translations)


def test_long_translation_stays_inside_its_area(sample_pdf, fake_translator):
    """번역문이 원문보다 훨씬 길어도 다른 문단·그림과 겹치지 않고, 조정 내용이 보고된다."""
    fake_translator.ratio = 1.6
    src, content, translations, engine = _setup(sample_pdf, fake_translator)
    doc, report, diffs, plans, accepted = layout_page(src, content, translations, engine)
    assert not [d for d in diffs if d.kind in ("overlap", "overflow", "original_text_left")
                and not any(a.kind == d.kind for a in accepted.get(d.paragraph_id, []))]
    adjusted = [pl for pl in plans.values() if pl.adjustments]
    assert adjusted, "긴 번역문은 줄바꿈/빈 공간/크기 조정이 필요하다"
    # 크기는 꼭 필요할 때만 줄이고, 그때는 보고서에 남는다
    for pid, pl in plans.items():
        para = next(p for p in content.paragraphs if p.id == pid)
        if pl.font_size < para.font_size:
            assert any("글자 크기 축소" in a for a in pl.adjustments)
            assert any(a.kind == "font_size" for a in accepted.get(pid, []))


def test_missing_glyph_uses_fallback_font(sample_pdf, dict_translator):
    src, content, translations, engine = _setup(sample_pdf, dict_translator)
    pid = next(iter(translations))
    translations[pid].sentences = ["α와 β 값을 비교한다 ①"]
    doc, *_ , = layout_page(src, content, translations, engine)
    text = doc[0].get_text()
    assert "α" in text and "β" in text and "①" in text


def test_side_by_side_output(sample_pdf, dict_translator, tmp_path):
    out = tmp_path / "out.pdf"
    only = tmp_path / "ko.pdf"
    report_path = tmp_path / "report.json"
    report = translate_pdf(sample_pdf, out, dict_translator, translated_only_path=only, report_path=report_path)
    assert report.ok
    src, side, ko = fitz.open(sample_pdf), fitz.open(out), fitz.open(only)
    w, h = src[0].rect.width, src[0].rect.height
    assert len(side) == len(src) == len(ko)
    assert side[0].rect.width > 2 * w and side[0].rect.height >= h
    left = side[0].get_text(clip=fitz.Rect(0, 0, w, side[0].rect.height))
    right = side[0].get_text(clip=fitz.Rect(w + 1, 0, side[0].rect.width, side[0].rect.height))
    assert "Community Gardens in Small Cities" in left  # 왼쪽: 영어 원본
    assert "소도시의 공동체 텃밭" in right and "Community" not in right  # 오른쪽: 한국어 번역본
    data = json.loads(report_path.read_text(encoding="utf-8"))
    assert data["ok"] and data["pages"][0]["paragraphs"]
