"""Every format: scan -> redact (each mode) -> re-open -> nothing expected may remain; highlight opens too."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.docio import find_leaks, open_document  # noqa: E402
from app.engine import detect  # noqa: E402
from app.redact.operators import plan_redaction  # noqa: E402
from tests.make_samples import EXPECTED, build_all  # noqa: E402

OUT = ROOT / "tests" / "out"
FILES = build_all(OUT)


@pytest.fixture(scope="module", params=FILES, ids=lambda p: p.suffix)
def scanned(request):
    path: Path = request.param
    doc = open_document(path.name, path.read_bytes())
    res = detect(doc.segments)
    return path, doc, res


@pytest.mark.parametrize("mode", ["remove", "mask", "token", "fake"])
def test_redact_no_leak(scanned, mode):
    path, doc, res = scanned
    accepted = [f for f in res.findings if f.accepted]
    plan = plan_redaction(accepted, mode, seed=7)
    data, info = doc.render_redacted(plan.edits, sanitize=True)
    out = OUT / f"{path.stem}.{mode}{doc.ext}"
    out.write_bytes(data)
    expected = [(t, v) for v, t in EXPECTED]
    before = find_leaks(path.read_bytes(), path.name, expected)
    assert len(before) >= 4, f"{path.suffix}: the leak check sees too few values in the original ({before})"
    again = open_document(out.name, data)
    assert len(again.segments) >= len([s for s in doc.segments if s.kind != "metadata"]) * 0.8, "output lost content"
    leaks = find_leaks(data, out.name, expected)
    assert not leaks, f"{path.suffix} {mode}: still present {leaks}"
    assert info.get("missed", 0) == 0


def test_highlight_opens(scanned):
    path, doc, res = scanned
    spans: dict = {}
    for f in res.findings:
        if f.accepted:
            spans.setdefault(f.seg_id, []).append((f.start, f.end))
    data, info = doc.render_highlight(spans)
    out = OUT / f"{path.stem}.highlight{doc.highlight_ext}"
    out.write_bytes(data)
    if doc.highlight_ext == doc.ext:
        again = open_document(out.name, data)
        body = lambda d: "".join(s.text for s in d.segments if s.kind not in ("metadata", "comment"))  # noqa: E731
        assert body(again) == body(doc)


def test_no_false_positive_headers():
    path = next(p for p in FILES if p.suffix == ".docx")
    doc = open_document(path.name, path.read_bytes())
    texts = {f.text for f in detect(doc.segments).findings}
    assert not texts & {"ตำแหน่ง", "เบอร์โทร", "Project Manager", "Developer"}
