"""Page view: finding boxes on PDF pages, page images, and the /pages API."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pymupdf as fitz
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import config  # noqa: E402
from app.docio import pages  # noqa: E402

needs_font = pytest.mark.skipif(not config.PDF_THAI_FONT, reason="no Thai font for test PDFs")


def _html_pdf(html: str) -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    arch = fitz.Archive(os.path.dirname(config.PDF_THAI_FONT))
    css = (f"@font-face {{font-family: th; src: url('{os.path.basename(config.PDF_THAI_FONT)}');}} "
           "* {font-family: th; font-size: 13px; line-height: 1.6;}")
    page.insert_htmlbox(fitz.Rect(50, 50, 550, 800), html, css=css, archive=arch)
    return doc.tobytes()


def _words_pdf(words: list[str], gap: float = 0.0) -> bytes:
    """Thai words drawn one by one with no space glyph between them, the way LibreOffice exports them."""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_font(fontname="th", fontfile=config.PDF_THAI_FONT)
    font = fitz.Font(fontfile=config.PDF_THAI_FONT)
    x = 72.0
    for w in words:
        page.insert_text((x, 120), w, fontname="th", fontsize=14)
        x += font.text_length(w, fontsize=14) + gap
    return doc.tobytes()


@needs_font
def test_locate_whole_value_and_no_double_box():
    pdf = _html_pdf("<p>ผู้ติดต่อ สมชาย ใจดี โทร 081-234-5678</p><p>อีกครั้ง สมชาย</p>")
    boxes = pages.locate(pdf, [("สมชาย", ["a"]), ("สมชาย ใจดี", ["b"]), ("081-234-5678", ["c"])])
    by = {}
    for b in boxes:
        by.setdefault(b[5][0], []).append(b)
    assert len(by["b"]) == 1 and len(by["c"]) == 1
    # "สมชาย" alone is boxed only on the second line, not again inside "สมชาย ใจดี"
    assert len(by["a"]) == 1 and by["a"][0][2] > by["b"][0][2]
    x0, y0, x1, y1 = by["b"][0][1:5]
    assert 0 <= x0 < x1 and 0 <= y0 < y1 and all(b[0] == 0 for b in boxes)


@needs_font
def test_chain_joins_words_without_space_glyph():
    pdf = _words_pdf(["นางสาว", "วรรณา", "ศรีสุข"], gap=1.0)
    with fitz.open(stream=pdf, filetype="pdf") as d:
        assert not d[0].search_for("วรรณา ศรีสุข")   # the reason _chain exists
    boxes = pages.locate(pdf, [("วรรณา  ศรีสุข", ["n1"])])   # extra whitespace is normalized too
    assert len(boxes) == 1
    with fitz.open(stream=pdf, filetype="pdf") as d:
        first = d[0].search_for("วรรณา")[0]
        last = d[0].search_for("ศรีสุข")[0]
    _, x0, _, x1, _, ids = boxes[0]
    assert ids == ["n1"] and x0 <= first.x0 + 0.5 and x1 >= last.x1 - 0.5


@needs_font
def test_chain_rejects_words_far_apart():
    pdf = _words_pdf(["วรรณา", "ข้อความคั่นกลางยาวมาก", "ศรีสุข"])
    assert pages.locate(pdf, [("วรรณา ศรีสุข", ["n1"])]) == []


@needs_font
def test_chain_follows_value_wrapped_to_next_line():
    # LibreOffice wraps "5-4002-22175-28-9" after a dash: the end of line 1, then the start of line 2
    doc = fitz.open()
    page = doc.new_page()
    page.insert_font(fontname="th", fontfile=config.PDF_THAI_FONT)
    page.insert_text((300, 120), "เลขที่5-4002-", fontname="th", fontsize=14)
    page.insert_text((72, 142), "22175-28-9 ยื่นคำร้อง", fontname="th", fontsize=14)
    page.insert_text((72, 300), "22175-28-9", fontname="th", fontsize=14)   # same tail far below: not part of it
    pdf = doc.tobytes()
    boxes = pages.locate(pdf, [("5-4002-22175-28-9", ["id1"])])
    assert len(boxes) == 2 and all(b[5] == ["id1"] for b in boxes)
    first, second = sorted(boxes, key=lambda b: b[2])
    assert first[1] > 290 and second[1] < 80 and second[2] < 200


@needs_font
def test_page_sizes_and_png():
    pdf = _html_pdf("<p>หน้าเดียว</p>")
    sizes, total = pages.page_sizes(pdf)
    assert total == 1 and sizes[0]["w"] > 500 and sizes[0]["h"] > 700
    png = pages.page_png(pdf, 0, width_px=600)
    assert png[:4] == b"\x89PNG"
    with pytest.raises(IndexError):
        pages.page_png(pdf, 1)


def test_availability():
    assert pages.available("pdf")
    assert not pages.available("xlsx") and not pages.available("text")
    assert pages.to_pdf("pdf", b"%PDF") == b"%PDF"
    with pytest.raises(pages.PagesUnavailable):
        pages.to_pdf("xlsx", b"")


@needs_font
def test_pages_api():
    from fastapi.testclient import TestClient

    from app.main import app

    c = TestClient(app)
    pdf = _html_pdf("<p>ผู้ป่วย นายสมชาย ใจดี โทร 081-234-5678</p>")
    r = c.post("/api/jobs", files={"file": ("p.pdf", pdf, "application/pdf")}, data={"options": "{}"})
    assert r.status_code == 200, r.text
    jid = r.json()["id"]
    try:
        for _ in range(100):
            j = c.get(f"/api/jobs/{jid}").json()
            if j["status"] in ("done", "error"):
                break
            time.sleep(0.1)
        assert j["status"] == "done" and j["page_view"] is True
        F = j["findings"]
        assert F
        items = [{"text": f["text"], "ids": [f["id"]]} for f in F]
        src = c.post(f"/api/jobs/{jid}/pages", json={"what": "source", "items": items}).json()
        assert src["total"] == 1 and len(src["pages"]) == 1
        assert {i for b in src["boxes"] for i in b[5]} == {f["id"] for f in F}
        img = c.get(f"/api/jobs/{jid}/pages/{src['key']}/0.png?w=800")
        assert img.status_code == 200 and img.content[:4] == b"\x89PNG"
        assert c.get(f"/api/jobs/{jid}/pages/{src['key']}/5.png").status_code == 404
        assert c.get(f"/api/jobs/{jid}/pages/nokey/0.png").status_code == 404
        out = c.post(f"/api/jobs/{jid}/pages", json={"what": "redact", "accepted": [f["id"] for f in F], "mode": "token"}).json()
        assert out["key"] != src["key"] and "boxes" not in out
        png = c.get(f"/api/jobs/{jid}/pages/{out['key']}/0.png").content
        assert png[:4] == b"\x89PNG"
        assert c.post(f"/api/jobs/{jid}/pages", json={"what": "bogus"}).status_code == 400
    finally:
        c.delete(f"/api/jobs/{jid}")
