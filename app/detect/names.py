"""Layer 2a — Thai/English person names from honorific titles and name corpora.

Thai has no spaces between words, so "เรียนคุณสมชายจะ..." is tokenized with
PyThaiNLP: "คุณ" is a title only when the tokenizer emits it as its own word
(so "คุณภาพ", "คุณสมบัติ", "ขอบคุณ" never trigger).
"""
from __future__ import annotations

import re
from typing import Optional

from presidio_analyzer import AnalysisExplanation, EntityRecognizer, RecognizerResult
from pythainlp.tokenize import word_tokenize

from .thai_nlp import thai_family_names, thai_first_names, thai_stopwords, thai_words, tokenize

TH_RUN = re.compile(r"[ก-๎]+")
EN_NAME = re.compile(r"[A-Z][a-z]+(?:[ -][A-Z][a-z]+){0,2}")

FORMAL_TITLES = {"นาย", "นาง", "นางสาว", "เด็กชาย", "เด็กหญิง", "ว่าที่ร้อยตรี"}
SOFT_TITLES = {"คุณ"}  # also means "you" -> needs a stronger name signal
_DOTTED = sorted(
    ["ผศ.ดร.", "รศ.ดร.", "ศ.ดร.", "ผศ.", "รศ.", "ศ.", "ดร.", "น.ส.", "ด.ช.", "ด.ญ.", "นพ.", "พญ.", "ทพ.", "ทพญ.",
     "ภก.", "ภญ.", "ว่าที่ร.ต.", "ว่าที่ ร.ต.", "พล.อ.", "พล.ท.", "พล.ต.", "พล.ต.อ.", "พล.ต.ท.", "พล.ต.ต.",
     "พ.ต.อ.", "พ.ต.ท.", "พ.ต.ต.", "ร.ต.อ.", "ร.ต.ท.", "ร.ต.ต.", "พ.อ.", "พ.ท.", "พ.ต.", "ร.อ.", "ร.ท.", "ร.ต.",
     "จ.ส.อ.", "จ.ส.ท.", "จ.ส.ต.", "ส.ต.อ.", "ส.ต.ท.", "ส.ต.ต."],
    key=len, reverse=True,
)
DOTTED_RE = re.compile(r"(?<![ก-๎A-Za-z.])(?:" + "|".join(map(re.escape, _DOTTED)) + ")")
EN_TITLE_RE = re.compile(r"(?<![A-Za-z])(?:(?:Mr|Mrs|Ms|Miss|Dr|Prof)\.?\s+|K\.\s?|Khun\s+)(?=[A-Z])")

# Words that look like a surname slot but are not ("คุณสมชาย ทีมพัฒนา").
_NOT_SURNAME = {"ทีม", "ฝ่าย", "แผนก", "ผู้จัดการ", "หัวหน้า", "ตำแหน่ง", "โทร", "เบอร์", "อีเมล", "และ", "หรือ"}


def _is_th(ch: str) -> bool:
    return "ก" <= ch <= "๎"


def _take_tokens(run: str, max_len: int) -> str:
    """Leading tokens of a Thai run, stopping at a stopword or max_len."""
    out = ""
    for t in word_tokenize(run, engine="newmm", keep_whitespace=False):
        if t in thai_stopwords() or len(out) + len(t) > max_len:
            break
        out += t
    return out


def _thai_name_at(text: str, p: int, soft: bool) -> Optional[tuple[int, int, float]]:
    m = TH_RUN.match(text, p)
    if not m:
        return None
    run1 = m.group()
    first_tok = word_tokenize(run1, engine="newmm", keep_whitespace=False)[0]
    if first_tok in thai_stopwords():
        return None
    name1 = run1 if len(run1) <= 15 else _take_tokens(run1, 12)
    if not name1:
        return None
    known = name1 in thai_first_names() or first_tok in thai_first_names()
    common_word = name1 in thai_words() and not known
    end = p + len(name1)
    has_surname = False
    if name1 == run1 and text[end:end + 1] == " " and end + 1 < len(text) and _is_th(text[end + 1]):
        run2 = TH_RUN.match(text, end + 1).group()
        toks2 = word_tokenize(run2, engine="newmm", keep_whitespace=False)
        if toks2 and toks2[0] not in thai_stopwords() and toks2[0] not in _NOT_SURNAME:
            surname = run2 if len(run2) <= 20 else _take_tokens(run2, 20)
            if len(surname) >= 2:
                end = end + 1 + len(surname)
                has_surname = True
    if soft:
        if common_word and not has_surname:
            score = 0.35  # "คุณกด..." — probably the pronoun "you"; show unticked
        else:
            score = 0.9 if has_surname else 0.75
    else:
        score = 0.9 if has_surname else (0.55 if common_word else 0.75)
    return p, end, score


class TitleNameRecognizer(EntityRecognizer):
    def __init__(self):
        super().__init__(supported_entities=["PERSON"], name="th_title_name", supported_language="th")

    def load(self) -> None:
        pass

    def _result(self, s, e, score, how):
        return RecognizerResult(
            "PERSON", s, e, score,
            analysis_explanation=AnalysisExplanation(f"{self.name}:{how}", score),
            recognition_metadata={RecognizerResult.RECOGNIZER_NAME_KEY: f"{self.name}:{how}",
                                  RecognizerResult.RECOGNIZER_IDENTIFIER_KEY: self.id},
        )

    def analyze(self, text, entities, nlp_artifacts=None):
        if nlp_artifacts is not None and nlp_artifacts.tokens:
            tokens, idx = list(map(str, nlp_artifacts.tokens)), nlp_artifacts.tokens_indices
        else:
            tokens, idx = tokenize(text)
        out: list[RecognizerResult] = []

        # (title_end, soft) candidates
        cands: list[tuple[int, bool]] = []
        for tok, i in zip(tokens, idx):
            if tok in FORMAL_TITLES:
                cands.append((i + len(tok), False))
            elif tok in SOFT_TITLES:
                cands.append((i + len(tok), True))
        for m in DOTTED_RE.finditer(text):
            cands.append((m.end(), False))

        for title_end, soft in cands:
            p = title_end
            while p < len(text) and text[p] == " " and p - title_end < 2:
                p += 1
            if p >= len(text):
                continue
            if "A" <= text[p] <= "Z":
                m = EN_NAME.match(text, p)
                if m:
                    out.append(self._result(m.start(), m.end(), 0.85, "title_en"))
                continue
            hit = _thai_name_at(text, p, soft)
            if hit:
                out.append(self._result(hit[0], hit[1], hit[2], "title_th"))

        for m in EN_TITLE_RE.finditer(text):
            n = EN_NAME.match(text, m.end())
            if n:
                out.append(self._result(n.start(), n.end(), 0.85, "title_en"))

        # Untitled "Firstname Surname": Thai full names are written as two space-separated runs.
        # Match whole runs (newmm splits unknown names: "สม|ชาย"), first run in the name corpus.
        first_names, family, words = thai_first_names(), thai_family_names(), thai_words()
        for m in TH_RUN.finditer(text):
            run1, i = m.group(), m.start()
            if run1 not in first_names or len(run1) < 2:
                continue
            end = m.end()
            if text[end:end + 1] != " " or end + 1 >= len(text) or not _is_th(text[end + 1]):
                continue
            run2 = TH_RUN.match(text, end + 1).group()
            first2 = word_tokenize(run2, engine="newmm", keep_whitespace=False)[0]
            if first2 in thai_stopwords() or first2 in _NOT_SURNAME or run2 in _NOT_SURNAME or len(run2) > 20 or len(run2) < 2:
                continue
            in_family = run2 in family
            if run2 != first2 and first2 in words and not in_family:
                continue  # "สมชาย เลขบัตรประชาชน": the next run is ordinary words, not a surname
            if run1 in words and not in_family:
                continue  # "น้ำ ประปา": the first run is also an ordinary word -> need a known surname
            if in_family:
                score = 0.8
            elif run2 not in words and len(run2) >= 3:
                score = 0.6
            else:
                score = 0.45
            out.append(self._result(i, end + 1 + len(run2), score, "name_corpus"))
        return out
