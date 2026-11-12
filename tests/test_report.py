"""Org presets, per-job law map and the evidence report. All sample values are fictional."""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.models import ENTITY_TYPES, Finding  # noqa: E402
from app.packs import DEFAULT_PACKS, PACK_IDS, PRESETS, clean_pack_ids, law_map, preset_of  # noqa: E402
from app.report import build_report  # noqa: E402


# ---------------------------------------------------------------- presets
def test_presets_are_valid_and_distinct():
    ids = [r["id"] for r in PRESETS]
    assert len(ids) == len(set(ids))
    seen = set()
    for r in PRESETS:
        assert r["name"] and r["desc"]
        assert r["packs"] == clean_pack_ids(r["packs"])
        key = tuple(sorted(r["packs"]))
        assert key not in seen, r["id"]
        seen.add(key)


def test_preset_of():
    assert preset_of(DEFAULT_PACKS) == "default"
    assert preset_of(PACK_IDS) == "all"
    for r in PRESETS:
        assert preset_of(r["packs"]) == r["id"]
        assert preset_of(list(reversed(r["packs"]))) == r["id"]
    assert preset_of(["pdpa_general"]) == ""


def test_law_map_limits_to_job_packs():
    full = law_map()
    assert set(full) == set(ENTITY_TYPES)
    only = law_map(["pdpa_general"])
    assert any(only.values())
    assert all(set(v) <= set(full[t]) for t, v in only.items())
    assert sum(map(len, only.values())) < sum(map(len, full.values()))


# ---------------------------------------------------------------- report
SECRET_NAME = "สมชาย ใจดีเกินเหตุ"
SECRET_ID = "1101700203451"


def _report(**kw):
    system = [
        Finding("s1", 0, 18, SECRET_NAME, "PERSON", 0.9, ["rule"], id="a"),
        Finding("s1", 30, 43, SECRET_ID, "TH_ID_CARD", 1.0, ["rule"], id="b"),
        Finding("s1", 50, 60, "0812345678", "PHONE", 0.4, ["rule"], id="c"),
    ]
    chosen = system[:2] + [Finding("s1", 70, 80, "ลับเฉพาะมาก", "PERSON", 1.0, ["manual"], id="m")]
    out = {"doc.redacted.docx": b"redacted-bytes", "doc.mapping.csv": b"csv"}
    args = dict(job_id="abcdef0123456789", stem="doc", ext=".docx", created=1_700_000_000.0, action="redact",
                mode="mask", type_modes={"TH_ID_CARD": "remove"}, sanitize=True,
                options={"packs": DEFAULT_PACKS, "entities": ["PERSON", "TH_ID_CARD", "PHONE", "EMAIL"]},
                models=[], system=system, chosen=chosen, leaks=0, outputs=out, warnings=[],
                has_mapping=True, stem_changed=False)
    args.update(kw)
    return build_report(**args).decode("utf-8"), out


def test_report_has_no_values():
    html, _ = _report()
    for v in (SECRET_NAME, SECRET_ID, "0812345678", "ลับเฉพาะมาก", "สมชาย"):
        assert v not in html


def test_report_contents():
    html, out = _report()
    assert html.startswith("<!doctype html>")
    assert "abcdef012345" in html
    assert "พ.ร.บ.คุ้มครองข้อมูลส่วนบุคคล" in html
    for data in out.values():
        assert hashlib.sha256(data).hexdigest() in html
    assert "ตรวจแล้วไม่พบ: อีเมล" in html


def test_report_empty_and_highlight():
    html, _ = _report(system=[], chosen=[], action="highlight", outputs={})
    assert "ไม่พบข้อมูลส่วนบุคคล" in html
