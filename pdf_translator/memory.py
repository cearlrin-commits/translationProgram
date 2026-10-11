"""번역 사전(번역 메모리).

{"영어 문장": "한국어 문장"} 형식의 JSON 파일이다. 번역할 때 먼저 이 사전을 찾아보고,
사전에 없는 문장만 번역 엔진에 보낸다. 엔진이 번역한 문장 중 문장/문단 검사를 통과한 것은
사전에 추가되어, 같은 문장이 다시 나오면 번역 엔진 없이 그대로 쓴다.

사전 파일을 직접 고쳐서 번역을 다듬을 수 있다. 사전에 있는 번역은 사람이 확인한 것으로 보고
다시 번역하거나 덮어쓰지 않는다.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


def normalize(sentence: str) -> str:
    return " ".join(sentence.split())


class TranslationMemory:
    def __init__(self, path: str | Path | None = None, entries: dict[str, str] | None = None):
        self.path = Path(path) if path else None
        self.entries: dict[str, str] = {}
        for src, tgt in (entries or {}).items():
            if normalize(src) and tgt and tgt.strip():
                self.entries[normalize(src)] = tgt.strip()
        self.hits = 0  # 사전에서 찾아 쓴 문장 수
        self.added = 0  # 이번 실행에서 새로 추가된 문장 수

    @classmethod
    def load(cls, path: str | Path) -> "TranslationMemory":
        """파일이 있으면 읽고, 없으면 빈 사전을 만든다(저장할 때 파일이 생긴다)."""
        path = Path(path)
        entries = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        if not isinstance(entries, dict):
            raise ValueError(f"{path}: 번역 사전은 {{\"영어 문장\": \"한국어 문장\"}} 형식이어야 합니다.")
        return cls(path, entries)

    def __len__(self) -> int:
        return len(self.entries)

    def __contains__(self, sentence: str) -> bool:
        return normalize(sentence) in self.entries

    def get(self, sentence: str) -> str | None:
        value = self.entries.get(normalize(sentence))
        if value is not None:
            self.hits += 1
        return value

    def add(self, source: str, target: str) -> bool:
        """새 문장만 추가한다. 이미 있는 번역(사람이 고쳤을 수 있음)은 덮어쓰지 않는다."""
        key = normalize(source)
        target = (target or "").strip()
        if not key or not target or key in self.entries:
            return False
        self.entries[key] = target
        self.added += 1
        return True

    def save(self, path: str | Path | None = None) -> None:
        path = Path(path) if path else self.path
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        data = json.dumps(self.entries, ensure_ascii=False, indent=2) + "\n"
        # 저장 도중 중단돼도 기존 사전이 깨지지 않도록 임시 파일에 쓴 뒤 바꿔치기한다
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(data)
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
