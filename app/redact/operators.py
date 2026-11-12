"""Turn accepted findings into concrete edits (redact) or marks (highlight).

Values are grouped by (type, normalised value), so the same person/phone gets
the same token or fake everywhere in the document. SECRET is always removed
when redacting and never written to the restore map.
"""
from __future__ import annotations

import csv
import io
import re
from collections import defaultdict
from dataclasses import dataclass, field

from ..detect.gazetteer import norm_key
from ..models import ENTITY_TYPES, MODES, Finding
from ..normalize import normalize
from .fake_th import FakeTH

REMOVED = "████"
_DIGIT_TYPES = {"PHONE", "TH_ID_CARD", "BANK_ACCOUNT", "CREDIT_CARD"}
_ALNUM = re.compile(r"[0-9A-Za-zก-๎]")  # Thai vowels/tone marks too, so "ใจดี" never shows as "ใจxี"


def value_key(entity_type: str, text: str) -> str:
    t = normalize(text)
    if entity_type in _DIGIT_TYPES:
        d = re.sub(r"\D", "", t)
        if entity_type == "PHONE" and d.startswith("66") and len(d) in (11, 12):
            d = "0" + d[2:]
        return d or norm_key(t)
    return norm_key(t)


def _mask_chars(value: str, keep_start: int, keep_end: int) -> str:
    idx = [i for i, ch in enumerate(value) if _ALNUM.match(ch)]
    keep = set(idx[:keep_start]) | (set(idx[-keep_end:]) if keep_end else set())
    return "".join("x" if (i in idx and i not in keep) else ch for i, ch in enumerate(value)) if idx else value


def mask_value(entity_type: str, value: str) -> str:
    v = value
    if entity_type == "EMAIL" and "@" in v:
        local, _, domain = v.partition("@")
        parts = domain.rsplit(".", 1)
        dom = _mask_chars(parts[0], 0, 0) + ("." + parts[1] if len(parts) == 2 else "")
        return _mask_chars(local, 1, 0) + "@" + dom
    if entity_type == "PHONE":
        return _mask_chars(v, 3, 2)
    if entity_type in ("CREDIT_CARD", "BANK_ACCOUNT"):
        return _mask_chars(v, 0, 4)
    if entity_type == "TH_ID_CARD":
        return _mask_chars(v, 1, 2)
    if entity_type == "IP_ADDRESS":
        octs = v.split(".")
        return ".".join(octs[:2] + ["xxx"] * (len(octs) - 2)) if len(octs) == 4 else _mask_chars(v, 0, 0)
    if entity_type in ("PERSON", "ORGANIZATION"):
        return " ".join(_mask_chars(w, 1, 0) for w in v.split(" "))
    if entity_type in ("SECRET", "ADDRESS", "DATE_OF_BIRTH"):
        return "xxxx"
    return _mask_chars(v, 1, 1)


@dataclass
class Plan:
    edits: dict[str, list[tuple[int, int, str]]] = field(default_factory=lambda: defaultdict(list))
    mapping: dict[str, str] = field(default_factory=dict)       # token -> original (token mode only)
    replaced: dict[str, str] = field(default_factory=dict)      # finding id -> replacement text
    originals: set[str] = field(default_factory=set)            # normalised originals, for the leak check
    counts: dict[str, int] = field(default_factory=lambda: defaultdict(int))


def _link_partial_names(groups: dict[tuple, list[Finding]]) -> dict[tuple, tuple]:
    """'สมชาย' alone -> the same token as 'สมชาย ใจดี' when exactly one full name matches."""
    full = [k for k in groups if k[0] == "PERSON" and " " in k[1]]
    link = {}
    for k in groups:
        if k[0] != "PERSON" or " " in k[1] or len(k[1]) < 3:
            continue
        cands = [f for f in full if k[1] in f[1].split(" ")]
        if len(cands) == 1:
            link[k] = cands[0]
    return link


def plan_redaction(findings: list[Finding], mode: str, type_modes: dict[str, str] | None = None, seed: int | None = None) -> Plan:
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode}")
    type_modes = {k: v for k, v in (type_modes or {}).items() if v in MODES}
    groups: dict[tuple, list[Finding]] = defaultdict(list)
    for f in findings:
        groups[(f.entity_type, value_key(f.entity_type, f.text))].append(f)
    link = _link_partial_names(groups)
    # deterministic numbering: order of first appearance (callers pass findings in document order)
    first = {}
    for i, f in enumerate(findings):
        first.setdefault((f.entity_type, value_key(f.entity_type, f.text)), i)
    order = sorted(groups, key=lambda k: first[k])
    numbers: dict[str, int] = defaultdict(int)
    token_of: dict[tuple, str] = {}
    fake = FakeTH(seed)
    fake_of: dict[tuple, str] = {}
    plan = Plan()
    for key in order:
        etype = key[0]
        canon = link.get(key, key)
        m = "remove" if etype == "SECRET" else type_modes.get(etype, mode)
        for f in groups[key]:
            plan.originals.add(normalize(f.text))
            if m == "remove":
                new = REMOVED
            elif m == "mask":
                new = mask_value(etype, f.text)
            elif m == "token":
                if canon not in token_of:
                    numbers[etype] += 1
                    token_of[canon] = f"[{etype}_{numbers[etype]}]"
                new = token_of[canon]
                full_text = groups[canon][0].text if canon in groups else f.text
                plan.mapping.setdefault(new, full_text)
            else:
                if canon not in fake_of:
                    fake_of[canon] = fake.make(etype, groups[canon][0].text if canon in groups else f.text)
                new = fake_of[canon]
                if canon != key:  # first name only -> first word of the fake full name
                    new = new.split(" ")[0]
            plan.edits[f.seg_id].append((f.start, f.end, new))
            plan.replaced[f.id] = new
            plan.counts[etype] += 1
    for seg_id in plan.edits:
        plan.edits[seg_id].sort()
    return plan


def apply_edits(text: str, edits: list[tuple[int, int, str]]) -> str:
    out, pos = [], 0
    for s, e, new in sorted(edits):
        if s < pos:
            continue
        out.append(text[pos:s])
        out.append(new)
        pos = e
    out.append(text[pos:])
    return "".join(out)


def findings_csv(findings: list[Finding], seg_labels: dict[str, str], replaced: dict[str, str] | None, highlight: bool) -> bytes:
    from ..engine import LAYER_LABELS, primary_layer

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["ลำดับ", "ประเภท", "ตำแหน่ง", "ค่า (ปิดบางส่วน)", "คะแนน", "พบโดย", "ผลลัพธ์"])
    for i, f in enumerate(findings, 1):
        res = "ไฮไลต์" if highlight else (replaced or {}).get(f.id, "")
        if f.entity_type == "SECRET" and not highlight:
            res = "ลบทิ้ง"
        w.writerow([i, ENTITY_TYPES.get(f.entity_type, {}).get("label", f.entity_type), seg_labels.get(f.seg_id, ""),
                    mask_value(f.entity_type, f.text), f"{f.score:.2f}", LAYER_LABELS.get(primary_layer(f), ""), res])
    return ("﻿" + buf.getvalue()).encode("utf-8")
