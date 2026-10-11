"""PDF에서 추출한 텍스트 구조와 번역/배치 결과를 담는 자료형."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

Rect = tuple[float, float, float, float]  # (x0, y0, x1, y1)
Color = tuple[float, float, float]  # 0~1 RGB


@dataclass
class Span:
    text: str
    size: float
    font: str
    bold: bool
    italic: bool
    color: Color
    bbox: Rect


@dataclass
class Line:
    spans: list[Span]
    bbox: Rect
    baseline: float

    @property
    def text(self) -> str:
        return "".join(s.text for s in self.spans)


@dataclass
class Paragraph:
    """원본 PDF의 한 문단(배치 단위). 번역본은 이 영역 안에 다시 배치된다."""

    id: str
    page: int
    lines: list[Line]
    text: str
    font_size: float
    color: Color
    bold: bool
    align: str  # left | right | center | justify
    line_pitch: float  # 줄 간격(기준선 사이 거리)
    first_indent: float  # 첫 줄 들여쓰기
    translatable: bool
    sentences: list[str] = field(default_factory=list)

    @property
    def bbox(self) -> Rect:
        xs0, ys0, xs1, ys1 = zip(*(ln.bbox for ln in self.lines))
        return (min(xs0), min(ys0), max(xs1), max(ys1))

    @property
    def first_baseline(self) -> float:
        return self.lines[0].baseline

    @property
    def span_rects(self) -> list[Rect]:
        return [s.bbox for ln in self.lines for s in ln.spans]


@dataclass
class PageContent:
    number: int
    width: float
    height: float
    paragraphs: list[Paragraph]
    # 텍스트 외 요소(이미지/도형) 영역. 번역문이 넘쳐 들어가면 안 되는 곳.
    obstacles: list[Rect] = field(default_factory=list)


@dataclass
class TranslationIssue:
    level: str  # sentence | paragraph
    paragraph_id: str
    code: str
    message: str
    sentence_index: Optional[int] = None


@dataclass
class TranslatedParagraph:
    paragraph_id: str
    source_sentences: list[str]
    sentences: list[str]
    issues: list[TranslationIssue] = field(default_factory=list)  # 최종적으로 남은 문제
    retries: int = 0

    @property
    def text(self) -> str:
        return " ".join(s.strip() for s in self.sentences if s.strip())
