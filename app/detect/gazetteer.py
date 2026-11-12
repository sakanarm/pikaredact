"""Layer 3 — known names (employee directory, customers) and the allowlist.

List files live in data/lists/*.txt, one entry per line. On a persons line,
variants of the same person can be separated by "|" or a tab, e.g.
    สมชาย ใจดี | Somchai Jaidee | พี่ชาย
Lines starting with # are comments.
"""
from __future__ import annotations

import re
import threading
from pathlib import Path

import ahocorasick
from presidio_analyzer import AnalysisExplanation, EntityRecognizer, RecognizerResult

from ..config import LISTS_DIR
from ..normalize import normalize

LIST_FILES = {
    "persons": ("PERSON", "รายชื่อบุคคล (พนักงาน/ลูกค้า) — 1 คนต่อบรรทัด คั่นชื่อหลายแบบด้วย |"),
    "organizations": ("ORGANIZATION", "ชื่อองค์กร/ลูกค้า — 1 ชื่อต่อบรรทัด"),
    "allowlist": (None, "คำที่ไม่ต้องปิดบัง (ชื่อผลิตภัณฑ์ บทบาท บริษัทเรา ฯลฯ)"),
}

_LATIN = re.compile(r"[A-Za-z0-9]")
_lock = threading.Lock()
_state: dict = {"version": -1}
_version = 0


def list_path(name: str) -> Path:
    return LISTS_DIR / f"{name}.txt"


def read_list(name: str) -> str:
    p = list_path(name)
    return p.read_text(encoding="utf-8") if p.exists() else ""


def write_list(name: str, content: str) -> None:
    global _version
    LISTS_DIR.mkdir(parents=True, exist_ok=True)
    list_path(name).write_text(content, encoding="utf-8")
    with _lock:
        _version += 1


def _entries(name: str) -> list[list[str]]:
    rows = []
    for line in read_list(name).splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [normalize(p).strip() for p in re.split(r"\s*[|\t]\s*", line) if p.strip()]
        if parts:
            rows.append(parts)
    return rows


def norm_key(text: str) -> str:
    return re.sub(r"\s+", " ", normalize(text)).strip().lower()


def _build():
    automaton = ahocorasick.Automaton()
    terms: dict[str, tuple[str, float]] = {}

    def add(term: str, etype: str, score: float):
        key = norm_key(term)
        is_latin = bool(_LATIN.search(key))
        if len(key) < (3 if is_latin else 4):
            return
        if key not in terms or terms[key][1] < score:
            terms[key] = (etype, score)

    for row in _entries("persons"):
        for variant in row:
            add(variant, "PERSON", 0.95 if " " in variant.strip() else 0.8)
            parts = variant.split()
            if len(parts) >= 2:  # first name / surname alone still identify the person
                for part in parts:
                    add(part, "PERSON", 0.6)
    for row in _entries("organizations"):
        for variant in row:
            add(variant, "ORGANIZATION", 0.9)
    for key, (etype, score) in terms.items():
        automaton.add_word(key, (key, etype, score))
    if terms:
        automaton.make_automaton()
    allow = {norm_key(v) for row in _entries("allowlist") for v in row}
    return automaton if terms else None, allow


def _current():
    with _lock:
        if _state["version"] != _version:
            _state["automaton"], _state["allow"] = _build()
            _state["version"] = _version
        return _state["automaton"], _state["allow"]


def allowlist() -> set[str]:
    return _current()[1]


def is_allowed(text: str) -> bool:
    return norm_key(text) in allowlist()


class GazetteerRecognizer(EntityRecognizer):
    def __init__(self):
        super().__init__(supported_entities=["PERSON", "ORGANIZATION"], name="gazetteer", supported_language="th")

    def load(self) -> None:
        pass

    def analyze(self, text, entities, nlp_artifacts=None):
        automaton, _ = _current()
        if automaton is None:
            return []
        low = text.lower()
        out = []
        for end_idx, (key, etype, score) in automaton.iter(low):
            s, e = end_idx - len(key) + 1, end_idx + 1
            if _LATIN.search(key):  # Latin terms need word boundaries
                if (s > 0 and low[s - 1].isascii() and low[s - 1].isalnum()) or (e < len(low) and low[e].isascii() and low[e].isalnum()):
                    continue
            out.append(RecognizerResult(
                etype, s, e, score,
                analysis_explanation=AnalysisExplanation("gazetteer", score),
                recognition_metadata={RecognizerResult.RECOGNIZER_NAME_KEY: "gazetteer",
                                      RecognizerResult.RECOGNIZER_IDENTIFIER_KEY: self.id},
            ))
        return out
