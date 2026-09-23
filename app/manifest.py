"""Versioned lesson manifest: reviewed content, loaded once, matched by exact normalized word + language."""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .textnorm import normalize


@dataclass
class Lesson:
    id: str
    language: str
    word: str
    normalized: str
    teaching_chunks: list[str]
    lesson_class: str
    display_prompt: str
    speech_script: str
    audio_asset: str
    review_status: str
    card: str = ""
    parts: list["Lesson"] | None = None    # a phrase lesson: one part per word

    @property
    def approved(self) -> bool:
        return self.review_status == "approved_for_demo"

    def to_dict(self) -> dict:
        return {
            "id": self.id, "language": self.language, "word": self.word,
            "teaching_chunks": self.teaching_chunks, "lesson_class": self.lesson_class,
            "display_prompt": self.display_prompt, "speech_script": self.speech_script,
            "audio_asset": self.audio_asset, "review_status": self.review_status, "card": self.card,
            "parts": [p.word for p in self.parts] if self.parts else None,
        }


class Manifest:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.version: str = ""
        self.lessons: list[Lesson] = []
        self._by_key: dict[tuple[str, str], Lesson] = {}
        self._by_id: dict[str, Lesson] = {}
        self.reload()

    def reload(self) -> None:
        data = yaml.safe_load(self.path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            self.version = str(data.get("version", ""))
            items = data.get("lessons", [])
        else:
            items = data or []
        self.lessons, self._by_key, self._by_id = [], {}, {}
        for it in items:
            word = unicodedata.normalize("NFC", it["word"])
            lesson = Lesson(
                id=it["id"], language=it["language"], word=word,
                normalized=normalize(it.get("normalized", word)),
                teaching_chunks=[unicodedata.normalize("NFC", c) for c in it.get("teaching_chunks", [])],
                lesson_class=it.get("lesson_class", ""), display_prompt=it.get("display_prompt", " + ".join(it.get("teaching_chunks", []))),
                speech_script=it.get("speech_script", word), audio_asset=it.get("audio_asset", ""),
                review_status=it.get("review_status", "draft"), card=it.get("card", ""),
            )
            key = (lesson.language, lesson.normalized)
            if key in self._by_key:
                raise ValueError(f"Duplicate manifest entry for {key}")
            self._by_key[key] = lesson
            self._by_id[lesson.id] = lesson
            self.lessons.append(lesson)

    def lookup(self, language: str, normalized_word: str) -> Lesson | None:
        return self._by_key.get((language, normalized_word))

    def by_id(self, lesson_id: str) -> Lesson | None:
        return self._by_id.get(lesson_id)

    def for_language(self, language: str) -> list[Lesson]:
        return [l for l in self.lessons if l.language == language]
