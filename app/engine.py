"""Detection pipeline: Presidio (rules + Thai NER + lists) -> tables -> LLM -> propagate -> allowlist -> merge.

Every layer produces Finding objects on *normalised* segment text. Normalisation
is length-preserving, so offsets apply to the original text unchanged.
"""
from __future__ import annotations

import bisect
import re
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from functools import lru_cache

from presidio_analyzer import AnalyzerEngine, RecognizerRegistry, RecognizerResult

from . import config
from .detect.gazetteer import GazetteerRecognizer, allowlist, norm_key
from .detect.llm import Profile, llm_findings
from .detect.names import TitleNameRecognizer
from .detect.rules import build_rule_recognizers
from .detect.sensitive import build_sensitive_recognizers
from .detect.tables import table_findings
from .detect.thai_nlp import ThaiNlpEngine, tokenize
from .models import ENTITY_TYPES, SENSITIVE_TYPES, TYPE_PRIORITY, Finding, Segment
from .normalize import normalize

SEP = "\n§\n"
CHUNK_CHARS = 50_000
LAYER_ORDER = ["rule", "list", "ner", "table", "llm", "propagate", "manual"]
LAYER_LABELS = {
    "rule": "กฎและ checksum", "list": "รายชื่อที่รู้จัก", "ner": "ชื่อและที่อยู่ไทย (NER)", "table": "โครงสร้างตาราง",
    "llm": "AI ภายในองค์กร", "propagate": "ค่าเดียวกันที่อื่นในเอกสาร", "manual": "คนเพิ่มเอง",
}


def layer_of(source: str) -> str:
    if source.startswith("th_title_name"):
        return "ner"
    if source.startswith("gazetteer"):
        return "list"
    if source.startswith("table:"):
        return "table"
    if source == "llm" or source.startswith("llm:"):
        return "llm"
    if source in ("propagate", "manual"):
        return source
    return "rule"


def models_of(f: Finding) -> list[str]:
    """'profile/model' for every LLM that reported this finding."""
    return sorted({s[4:] for s in f.sources if s.startswith("llm:")})


def primary_layer(f: Finding) -> str:
    layers = {layer_of(s) for s in f.sources}
    return next((l for l in LAYER_ORDER if l in layers), "rule")


@lru_cache(maxsize=1)
def analyzer() -> AnalyzerEngine:
    registry = RecognizerRegistry(supported_languages=["th"])
    for rec in build_rule_recognizers() + build_sensitive_recognizers():
        registry.add_recognizer(rec)
    registry.add_recognizer(TitleNameRecognizer())
    registry.add_recognizer(GazetteerRecognizer())
    nlp = ThaiNlpEngine()
    nlp.load()
    return AnalyzerEngine(registry=registry, nlp_engine=nlp, supported_languages=["th"], default_score_threshold=0.0)


def _source(r: RecognizerResult) -> str:
    md = r.recognition_metadata or {}
    return str(md.get(RecognizerResult.RECOGNIZER_NAME_KEY, "rule"))


def analyze_text(text: str, entities: list[str] | None = None) -> list[RecognizerResult]:
    """Presidio on one string (used by the Presidio-compatible /analyze route)."""
    ents = [e for e in (entities or []) if e in ENTITY_TYPES] or None
    return analyzer().analyze(text=normalize(text), language="th", entities=ents, score_threshold=0.0)


def _presidio(segs: list[Segment], norms: list[str]) -> list[Finding]:
    out: list[Finding] = []
    eng = analyzer()
    i = 0
    while i < len(segs):
        starts, parts, size, j = [], [], 0, i
        while j < len(segs) and (not parts or size + len(norms[j]) + len(SEP) <= CHUNK_CHARS):
            starts.append(size)
            parts.append(norms[j])
            size += len(norms[j]) + len(SEP)
            j += 1
        joined = SEP.join(parts)
        if joined.strip():
            for r in eng.analyze(text=joined, language="th", score_threshold=0.0):
                k = bisect.bisect_right(starts, r.start) - 1
                seg, base = segs[i + k], starts[k]
                s, e = r.start - base, min(r.end - base, len(norms[i + k]))
                if e > s:
                    out.append(Finding(seg.id, s, e, seg.text[s:e], r.entity_type, float(r.score), [_source(r)]))
        i = j
    return out


_LATIN_EDGE = re.compile(r"[A-Za-z0-9]")


class _Bounds:
    """Lazy token boundaries per segment, so propagated Thai values match whole words only."""

    def __init__(self):
        self._cache: dict[str, set[int]] = {}

    def ok(self, seg_id: str, norm: str, s: int, e: int) -> bool:
        if s > 0 and _LATIN_EDGE.match(norm[s - 1]) and _LATIN_EDGE.match(norm[s]):
            return False
        if e < len(norm) and _LATIN_EDGE.match(norm[e]) and _LATIN_EDGE.match(norm[e - 1]):
            return False
        th = lambda ch: "฀" <= ch <= "๿"
        if not ((s > 0 and th(norm[s - 1]) and th(norm[s])) or (e < len(norm) and th(norm[e]) and th(norm[e - 1]))):
            return True
        if seg_id not in self._cache:
            toks, idx = tokenize(norm)
            self._cache[seg_id] = set(idx) | {len(norm)}
        b = self._cache[seg_id]
        return s in b and e in b


# Values that describe a person rather than identify them ("เบาหวาน", "35,000 บาท"): the same word elsewhere
# in the document is usually generic text, so it is not copied to every other occurrence.
NO_PROPAGATE = SENSITIVE_TYPES | {"SALARY", "CARD_SECURITY"}


def _propagate(segs: list[Segment], norms: list[str], findings: list[Finding]) -> list[Finding]:
    best: dict[str, Finding] = {}
    for f in findings:
        if f.score < config.AUTO_ACCEPT_SCORE or f.entity_type in NO_PROPAGATE:
            continue
        val = re.sub(r"\s+", " ", normalize(f.text)).strip()
        if len(val) < 4 or (val.replace("-", "").replace(" ", "").isdigit() and len(val) < 6):
            continue
        if val not in best or best[val].score < f.score:
            best[val] = f
    if not best:
        return []
    bounds = _Bounds()
    have = {(f.seg_id, f.start, f.end) for f in findings}
    out = []
    for val, src in best.items():
        for seg, norm in zip(segs, norms):
            start = 0
            while True:
                i = norm.find(val, start)
                if i < 0:
                    break
                e = i + len(val)
                if (seg.id, i, e) not in have and bounds.ok(seg.id, norm, i, e):
                    out.append(Finding(seg.id, i, e, seg.text[i:e], src.entity_type, src.score, ["propagate"]))
                start = e
    return out


def _allow_filter(segs_by_id: dict[str, str], findings: list[Finding]) -> list[Finding]:
    allow = allowlist()
    if not allow:
        return findings
    spans: dict[str, list[tuple[int, int]]] = {}

    def allowed_spans(seg_id):
        if seg_id not in spans:
            low = segs_by_id[seg_id].lower()
            res = []
            for term in allow:
                if term and term in low:
                    p = low.find(term)
                    while p >= 0:
                        res.append((p, p + len(term)))
                        p = low.find(term, p + 1)
            spans[seg_id] = res
        return spans[seg_id]

    out = []
    for f in findings:
        if norm_key(f.text) in allow:
            continue
        if any(s <= f.start and f.end <= e for s, e in allowed_spans(f.seg_id)):
            continue
        out.append(f)
    return out


_TRIM = re.compile(r"^[\s,.;:()\-–/|]+|[\s,.;:()\-–/|]+$")


def _trim(f: Finding, text: str) -> Finding | None:
    s, e = f.start, f.end
    while s < e and _TRIM.match(text[s]):
        s += 1
    while e > s and _TRIM.match(text[e - 1]):
        e -= 1
    if e - s < 1 or not re.search(r"[0-9A-Za-z฀-๿]", text[s:e]):
        return None
    return replace(f, start=s, end=e)


def merge(findings: list[Finding], seg_text: dict[str, str]) -> list[Finding]:
    by_seg: dict[str, list[Finding]] = defaultdict(list)
    for f in findings:
        by_seg[f.seg_id].append(f)
    out: list[Finding] = []
    for seg_id, fs in by_seg.items():
        text = seg_text[seg_id]
        # 1. same type + overlapping -> one finding covering the union
        fs = sorted(fs, key=lambda f: (f.entity_type, f.start, -f.end))
        unioned: list[Finding] = []
        for f in fs:
            last = unioned[-1] if unioned and unioned[-1].entity_type == f.entity_type else None
            if last and f.start < last.end:
                last.end = max(last.end, f.end)
                last.score = max(last.score, f.score)
                last.sources = sorted(set(last.sources) | set(f.sources))
            else:
                unioned.append(replace(f, sources=list(f.sources)))
        for f in unioned:
            layers = {layer_of(s) for s in f.sources} - {"propagate"}
            if len(layers) >= 2:
                f.score = min(1.0, f.score + 0.1)
            if len(models_of(f)) >= 2:  # several models agree
                f.score = min(1.0, f.score + 0.1)
        # 2. different types overlapping -> strongest wins, the rest keeps only uncovered parts
        unioned.sort(key=lambda f: (-round(f.score, 3), TYPE_PRIORITY.get(f.entity_type, 99), -(f.end - f.start)))
        kept: list[Finding] = []
        for f in unioned:
            pieces = [(f.start, f.end)]
            for k in kept:
                nxt = []
                for s, e in pieces:
                    if e <= k.start or k.end <= s:
                        nxt.append((s, e))
                        continue
                    if s < k.start:
                        nxt.append((s, k.start))
                    if k.end < e:
                        nxt.append((k.end, e))
                pieces = nxt
            for s, e in pieces:
                t = _trim(replace(f, start=s, end=e, sources=list(f.sources)), text)
                if t and (t.end - t.start >= 2 or (s, e) == (f.start, f.end)):
                    kept.append(t)
        for f in kept:
            f.text = text[f.start:f.end]
        out.extend(kept)
    return out


@dataclass
class DetectResult:
    findings: list[Finding]
    warnings: list[str] = field(default_factory=list)
    timings: dict[str, float] = field(default_factory=dict)
    llm_used: list[str] = field(default_factory=list)   # "profile/model" per model that ran
    models: list[dict] = field(default_factory=list)    # per model: found, seconds, warnings


def _vote(found: list[Finding], min_agree: int) -> list[Finding]:
    """Keep an LLM-only finding when at least min_agree models (or any other layer) found an overlapping span."""
    by_seg: dict[str, list[Finding]] = defaultdict(list)
    for f in found:
        by_seg[f.seg_id].append(f)
    out = []
    for f in found:
        if layer_of(f.sources[0]) != "llm":
            out.append(f)
            continue
        near = [g for g in by_seg[f.seg_id] if g.start < f.end and f.start < g.end]
        if any(layer_of(g.sources[0]) != "llm" for g in near):
            out.append(f)
        elif len({g.sources[0] for g in near}) >= min_agree:
            out.append(f)
    return out


def detect(
    segments: list[Segment],
    *,
    profile: Profile | None = None,
    profiles: list[Profile] | None = None,
    combine: str = "union",
    min_agree: int = 2,
    entities: list[str] | None = None,
    layers: dict | None = None,
    progress=None,
) -> DetectResult:
    """profiles: several LLMs run in parallel. combine="union" keeps everything any model found
    (recall first, +0.1 when two or more agree); combine="vote" keeps an LLM-only finding only when
    at least min_agree models found it. Rules/lists/NER findings are always kept."""
    t0 = time.perf_counter()
    prog = progress or (lambda stage, done=0, total=0: None)
    segs = [s for s in segments if s.text and s.text.strip()]
    norms = [normalize(s.text) for s in segs]
    seg_text = {s.id: s.text for s in segs}
    seg_norm = {s.id: n for s, n in zip(segs, norms)}
    timings: dict[str, float] = {}
    warnings: list[str] = []

    # layers: {"ner": bool, "list": bool}; rules are always on, LLM is chosen by `profiles`.
    off = {k for k in ("ner", "list") if layers and layers.get(k) is False}
    prog("rules")
    found = _presidio(segs, norms)
    if off:
        found = [f for f in found if not ({layer_of(s) for s in f.sources} <= off)]
    timings["rules"] = time.perf_counter() - t0

    t1 = time.perf_counter()
    if "ner" not in off:
        norm_segs = [replace(s, text=n) for s, n in zip(segs, norms)]
        found += table_findings(norm_segs)
    timings["tables"] = time.perf_counter() - t1

    plist = list(profiles or []) + ([profile] if profile is not None else [])
    plist = list({p.tag: p for p in plist}.values())
    models: list[dict] = []
    if plist:
        t2 = time.perf_counter()
        pairs = list(zip(segs, norms))
        state = {p.tag: (0, 1) for p in plist}

        def on_progress(tag):
            def cb(d, n):
                state[tag] = (d, n)
                prog("llm", sum(a for a, _ in state.values()), sum(b for _, b in state.values()))
            return cb

        def run(p):
            t = time.perf_counter()
            res, warn = llm_findings(p, pairs, progress=on_progress(p.tag))
            return p, res, warn, time.perf_counter() - t

        prog("llm", 0, len(plist))
        with ThreadPoolExecutor(max_workers=len(plist)) as ex:
            for p, res, warn, secs in ex.map(run, plist):
                found += res
                warnings += warn
                models.append({"model": f"{p.name}/{p.model}", "found": len(res), "seconds": round(secs, 2), "failed": bool(warn)})
        if combine == "vote" and len(plist) >= 2:
            found = _vote(found, max(2, min(min_agree, len(plist))))
        timings["llm"] = time.perf_counter() - t2

    prog("merge")
    t3 = time.perf_counter()
    allowed = None if entities is None else set(entities) | {"SECRET"}
    if allowed is not None:  # before merge, so a switched-off type cannot take a span from an enabled one
        found = [f for f in found if f.entity_type in allowed]
    found = _allow_filter(seg_norm, found)
    first = merge(found, seg_text)
    found = _allow_filter(seg_norm, found + _propagate(segs, norms, first))
    final = merge(found, seg_text)
    final = [f for f in final if f.score >= config.MIN_SCORE]
    if allowed is not None:
        final = [f for f in final if f.entity_type in allowed]
    order = {s.id: i for i, s in enumerate(segs)}
    final.sort(key=lambda f: (order.get(f.seg_id, 0), f.start))
    for i, f in enumerate(final, 1):
        f.id = f"f{i}"
        f.accepted = f.score >= config.AUTO_ACCEPT_SCORE
    timings["merge"] = time.perf_counter() - t3
    timings["total"] = time.perf_counter() - t0
    return DetectResult(final, warnings, {k: round(v, 3) for k, v in timings.items()}, [m["model"] for m in models], models)


def detect_text(text: str, **kw) -> tuple[list[Segment], DetectResult]:
    """Plain text: one segment per line, so review labels can say 'บรรทัด N'."""
    segs = [Segment(f"l{i}", line, f"บรรทัด {i + 1}", "ข้อความ") for i, line in enumerate(text.split("\n"))]
    return segs, detect(segs, **kw)
