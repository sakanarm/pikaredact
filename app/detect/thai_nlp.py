"""Thai-aware NLP engine for Presidio.

Presidio needs tokens + lemmas for context-word boosting. spaCy has no Thai
model, so we tokenize with PyThaiNLP (newmm) and expose tokens as lemmas.
No model download is needed, so the service can run fully offline.
"""
from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from typing import Iterable, Iterator, List, Optional, Tuple

from presidio_analyzer.nlp_engine import NlpArtifacts, NlpEngine
from pythainlp.tokenize import word_tokenize

_PUNCT = re.compile(r"^[\W_]+$", re.UNICODE)


@lru_cache(maxsize=1)
def thai_words() -> frozenset:
    from pythainlp.corpus import thai_words as _tw

    return frozenset(_tw())


@lru_cache(maxsize=1)
def thai_stopwords() -> frozenset:
    from pythainlp.corpus import thai_stopwords as _ts

    return frozenset(_ts())


@lru_cache(maxsize=1)
def thai_first_names() -> frozenset:
    out: set[str] = set()
    try:
        from pythainlp.corpus import thai_female_names, thai_male_names

        out |= set(thai_female_names()) | set(thai_male_names())
    except Exception:  # corpus missing -> rule simply gets weaker
        pass
    return frozenset(n for n in out if len(n) >= 3)


@lru_cache(maxsize=1)
def thai_family_names() -> frozenset:
    try:
        from pythainlp.corpus import thai_family_names as _fn

        return frozenset(n for n in _fn() if len(n) >= 3)
    except Exception:
        return frozenset()


def tokenize(text: str) -> Tuple[List[str], List[int]]:
    """Return tokens and their start offsets. Tokens concatenate back to text."""
    toks = word_tokenize(text, engine="newmm", keep_whitespace=True)
    idx, pos = [], 0
    for t in toks:
        idx.append(pos)
        pos += len(t)
    if pos != len(text):  # defensive: fall back to whitespace split
        toks, idx = [], []
        for m in re.finditer(r"\S+|\s+", text):
            toks.append(m.group())
            idx.append(m.start())
    return toks, idx


class ThaiNlpEngine(NlpEngine):
    def __init__(self):
        self._loaded = False

    def load(self) -> None:
        thai_words()
        thai_stopwords()
        self._loaded = True

    def is_loaded(self) -> bool:
        return self._loaded

    def process_text(self, text: str, language: str) -> NlpArtifacts:
        tokens, idx = tokenize(text)
        return NlpArtifacts(
            entities=[],
            tokens=tokens,
            tokens_indices=idx,
            lemmas=[t.lower() for t in tokens],
            nlp_engine=self,
            language=language,
        )

    def process_batch(self, texts: Iterable[str], language: str, batch_size: int = 1, n_process: int = 1, **kwargs) -> Iterator[Tuple[str, NlpArtifacts]]:
        for t in texts:
            yield t, self.process_text(t, language)

    def is_stopword(self, word: str, language: str) -> bool:
        # Whitespace tokens are "stopwords" so they never count toward the context window.
        return not word.strip()

    def is_punct(self, word: str, language: str) -> bool:
        return bool(_PUNCT.match(word)) and not any(unicodedata.category(c).startswith("L") for c in word)

    def get_supported_entities(self) -> List[str]:
        return []

    def get_supported_languages(self) -> List[str]:
        return ["th"]
