"""Page view: show Word / PowerPoint / PDF the way they print.

Office files are turned into PDF with headless LibreOffice, pages are drawn as PNG with
PyMuPDF, and each finding is located on the page so the review screen can draw a box over it.
Nothing here is written to disk outside a temp folder that is removed right away.
"""
from __future__ import annotations

import re
import threading

import pymupdf as fitz

from .text import soffice_convert, soffice_path

PAGE_KINDS = {"docx", "pptx", "pdf"}
MAX_PAGES = 80
_SEP = re.compile(r"[\s\-\u00ad\u2010-\u2015/.:]+")   # space, dashes (incl. soft hyphen), slash, dot, colon
_slots = threading.BoundedSemaphore(2)   # LibreOffice is heavy: at most two conversions at once


class PagesUnavailable(Exception):
    pass


def available(kind: str) -> bool:
    return kind == "pdf" or (kind in PAGE_KINDS and soffice_path() is not None)


def to_pdf(kind: str, data: bytes) -> bytes:
    if kind == "pdf":
        return data
    if kind not in PAGE_KINDS:
        raise PagesUnavailable("ไฟล์ชนิดนี้แสดงแบบหน้าเอกสารไม่ได้")
    if not soffice_path():
        raise PagesUnavailable("เซิร์ฟเวอร์นี้ยังไม่มี LibreOffice จึงแสดงแบบหน้าเอกสารไม่ได้ — ใช้มุมมองข้อความแทน")
    with _slots:
        pdf = soffice_convert("." + kind, data, "pdf", timeout=120)
    if not pdf:
        raise PagesUnavailable("แปลงเป็นหน้าเอกสารไม่สำเร็จ — ใช้มุมมองข้อความแทน")
    return pdf


def page_sizes(pdf: bytes) -> tuple[list[dict], int]:
    with fitz.open(stream=pdf, filetype="pdf") as d:
        n = len(d)
        return [{"w": round(d[i].rect.width, 2), "h": round(d[i].rect.height, 2)} for i in range(min(n, MAX_PAGES))], n


def page_png(pdf: bytes, index: int, width_px: int = 1100) -> bytes:
    with fitz.open(stream=pdf, filetype="pdf") as d:
        if not 0 <= index < min(len(d), MAX_PAGES):
            raise IndexError(index)
        page = d[index]
        z = max(0.5, min(3.0, width_px / max(page.rect.width, 1)))
        return page.get_pixmap(matrix=fitz.Matrix(z, z), alpha=False).tobytes("png")


def _chain(page, parts: list[str], cache: dict) -> list[fitz.Rect]:
    """Boxes for a value split into pieces. LibreOffice drops the space glyph between Thai words, so the
    whole value is not searchable: find the pieces one by one and keep runs that sit next to each other
    on the same line. A value may also wrap at a separator ("...@vendor-" / "demo.co.th"): then the next
    piece starts the following line, left of where the current piece is. Returns one rect per line."""
    def hits(p):
        if p not in cache:
            # one hit can come back as several rects (Thai vowels drawn apart): join pieces on the same line
            merged: list[fitz.Rect] = []
            for r in page.search_for(p):
                last = merged[-1] if merged else None
                if last is not None and abs(r.y0 - last.y0) < last.height * 0.6 and -2 <= r.x0 - last.x1 <= last.height * 0.6:
                    merged[-1] = last | r
                else:
                    merged.append(fitz.Rect(r))
            cache[p] = merged
        return cache[p]

    def same_line(c, cur, h):
        return abs(c.y0 - cur.y0) < h * 0.6 and -2 <= c.x0 - cur.x1 <= h * 1.6

    def next_line(c, cur, h):
        return cur.y1 - h * 0.3 <= c.y0 <= cur.y1 + h * 1.2 and c.x0 < cur.x0

    out = []
    for r in hits(parts[0]):
        cur, lines = r, [fitz.Rect(r)]
        for p in parts[1:]:
            h = max(cur.height, 1)
            cand = hits(p)
            nxt = next((c for c in cand if same_line(c, cur, h)), None)
            if nxt is not None:
                lines[-1] |= nxt
            else:
                nxt = next((c for c in cand if next_line(c, cur, h)), None)
                if nxt is None:
                    lines = None
                    break
                lines.append(fitz.Rect(nxt))
            cur = nxt
        if lines:
            out.extend(lines)
    return out


def locate(pdf: bytes, items: list[tuple[str, list[str]]]) -> list[list]:
    """items: (text, ids). Returns [page, x0, y0, x1, y1, ids] in PDF points, for every place a text is found."""
    boxes = []
    with fitz.open(stream=pdf, filetype="pdf") as d:
        for pno in range(min(len(d), MAX_PAGES)):
            page = d[pno]
            cache: dict = {}
            taken: list[fitz.Rect] = []
            # longest values first, so "สมชาย ใจดี" claims its place before "สมชาย" alone
            for text, ids in sorted(items, key=lambda it: -len(it[0])):
                t = re.sub(r"\s+", " ", text).strip()
                if len(t) < 2:
                    continue
                found = page.search_for(t)
                if not found:
                    # spaces may be missing and dashes may be drawn as another glyph (soft hyphen, en dash):
                    # look for the pieces between separators and join neighbours on the same line
                    parts = [x for x in _SEP.split(t) if x]
                    if len(parts) > 1:
                        found = _chain(page, parts, cache)
                for r in found:
                    if any((r & o).get_area() > 0.8 * r.get_area() for o in taken):
                        continue  # inside a longer value already boxed
                    taken.append(r)
                    boxes.append([pno, round(r.x0, 2), round(r.y0, 2), round(r.x1, 2), round(r.y1, 2), ids])
    return boxes
