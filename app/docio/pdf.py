"""PDF via PyMuPDF.

Text is read per block with a bounding box for every character, so a finding
maps to exact rectangles. Redaction is real (apply_redactions removes the glyphs
from the content stream, then the file is rewritten with garbage collection);
replacement text is drawn back with a Thai-capable font. Metadata, annotation
text/authors, form values, bookmarks and link URIs are scanned too.
"""
from __future__ import annotations

import os
import re
from collections import defaultdict

import pymupdf as fitz  # PyMuPDF (AGPL)

from .. import config
from ..models import Segment
from .base import Document

REMOVED = "████"
_FLAGS = fitz.TEXT_PRESERVE_WHITESPACE | fitz.TEXT_PRESERVE_LIGATURES | fitz.TEXT_MEDIABOX_CLIP
_META_LABELS = {"title": "ชื่อเรื่อง", "author": "ผู้เขียน", "subject": "หัวข้อ", "keywords": "คำสำคัญ", "creator": "โปรแกรมที่สร้าง", "producer": "ผู้ผลิต"}


class _PdfUnit:
    __slots__ = ("seg", "chars", "ref")

    def __init__(self, seg: Segment, ref: tuple):
        self.seg = seg
        self.chars: list = []   # per char: (page, line_key, (x0,y0,x1,y1), baseline_y) or None
        self.ref = ref          # ("text",) | ("meta", key) | ("annot", page, xref, field) | ("widget", page, xref) | ("toc", i) | ("link", page, i)


def _open(data: bytes) -> fitz.Document:
    doc = fitz.open(stream=data, filetype="pdf")
    if doc.needs_pass:
        raise ValueError("PDF นี้มีรหัสผ่าน — กรุณาปลดรหัสผ่านก่อนอัปโหลด")
    return doc


class PdfDocument(Document):
    kind = "pdf"
    ext = ".pdf"

    def __init__(self, filename: str, data: bytes):
        self.filename, self.data = filename, data
        self.warnings: list[str] = []
        self.units: list[_PdfUnit] = []
        doc = _open(data)
        try:
            self._extract(doc)
        finally:
            doc.close()

    @property
    def segments(self) -> list[Segment]:
        return [u.seg for u in self.units]

    # ------------------------------------------------------------ extraction
    def _new(self, sid, label, group, kind, ref) -> _PdfUnit:
        u = _PdfUnit(Segment(sid, "", label, group, kind), ref)
        self.units.append(u)
        return u

    def _plain(self, sid, text, label, group, kind, ref):
        if text and text.strip():
            u = self._new(sid, label, group, kind, ref)
            u.seg.text = text
            u.chars = [None] * len(text)

    def _extract(self, doc: fitz.Document):
        n_chars = n_images = 0
        for pno in range(doc.page_count):
            page = doc[pno]
            group = f"หน้า {pno + 1}"
            raw = page.get_text("rawdict", flags=_FLAGS)
            nb = 0
            for block in raw.get("blocks", []):
                if block.get("type") == 1:
                    n_images += 1
                    continue
                nb += 1
                u = _PdfUnit(Segment(f"p{pno + 1}b{nb}", "", f"หน้า {pno + 1} · ย่อหน้า {nb}", group, "body"), ("text",))
                for li, line in enumerate(block.get("lines", [])):
                    if li and u.seg.text and not u.seg.text.endswith("\n"):
                        u.seg.text += "\n"
                        u.chars.append(None)
                    for span in line.get("spans", []):
                        for ch in span.get("chars", []):
                            c = ch.get("c", "")
                            if not c:
                                continue
                            x0, y0, x1, y1 = ch["bbox"]
                            for k, cc in enumerate(c):  # ligatures may expand to several chars
                                u.seg.text += cc
                                u.chars.append((pno, (nb, li), (x0, y0, x1, y1), ch["origin"][1]))
                if u.seg.text.strip():
                    n_chars += len(u.seg.text.strip())
                    self.units.append(u)
            n_images += len(page.get_images(full=False))
            for annot in page.annots() or []:
                info = annot.info
                if annot.type[0] in (fitz.PDF_ANNOT_REDACT, fitz.PDF_ANNOT_LINK):
                    continue
                self._plain(f"p{pno + 1}a{annot.xref}c", info.get("content", ""), f"หน้า {pno + 1} · คอมเมนต์", group, "comment", ("annot", pno, annot.xref, "content"))
                self._plain(f"p{pno + 1}a{annot.xref}t", info.get("title", ""), f"หน้า {pno + 1} · ผู้เขียนคอมเมนต์", group, "metadata", ("annot", pno, annot.xref, "title"))
                self._plain(f"p{pno + 1}a{annot.xref}s", info.get("subject", ""), f"หน้า {pno + 1} · หัวข้อคอมเมนต์", group, "metadata", ("annot", pno, annot.xref, "subject"))
            for w in page.widgets() or []:
                val = w.field_value
                if isinstance(val, str) and val.strip() and w.field_type in (fitz.PDF_WIDGET_TYPE_TEXT, fitz.PDF_WIDGET_TYPE_COMBOBOX, fitz.PDF_WIDGET_TYPE_LISTBOX):
                    self._plain(f"p{pno + 1}w{w.xref}", val, f"หน้า {pno + 1} · ช่องฟอร์ม {w.field_name or ''}".strip(), group, "field", ("widget", pno, w.xref))
            for i, lnk in enumerate(page.get_links()):
                if lnk.get("uri"):
                    self._plain(f"p{pno + 1}l{i}", lnk["uri"], f"หน้า {pno + 1} · ลิงก์", "ลิงก์ในเอกสาร", "link", ("link", pno, i))
        meta = doc.metadata or {}
        for key, lab in _META_LABELS.items():
            self._plain(f"m_{key}", meta.get(key) or "", f"ข้อมูลไฟล์ · {lab}", "ข้อมูลไฟล์", "metadata", ("meta", key))
        for i, item in enumerate(doc.get_toc(simple=True)):
            self._plain(f"toc{i}", item[1], "สารบัญ (bookmark)", "สารบัญ", "metadata", ("toc", i))
        if doc.embfile_count():
            self.warnings.append(f"มีไฟล์แนบใน PDF {doc.embfile_count()} ไฟล์ — ยังไม่ได้ตรวจ และจะถูกลบออกเมื่อเลือกล้างข้อมูลไฟล์")
        if n_images:
            self.warnings.append(f"รูปภาพ {n_images} รูปยังไม่ได้ตรวจ — ข้อความในรูปรองรับใน Phase 2 (OCR) เปิดดูก่อนส่งต่อ")
        if doc.page_count and n_chars < 20 * doc.page_count and n_images:
            self.warnings.insert(0, "PDF นี้น่าจะเป็นภาพสแกน มีข้อความให้ตรวจน้อยมาก — การอ่านข้อความจากภาพ (OCR) รองรับใน Phase 2")

    # ------------------------------------------------------------ geometry
    @staticmethod
    def _rects(u: _PdfUnit, s: int, e: int) -> list[tuple[int, fitz.Rect, float]]:
        """Union of char boxes per visual line -> [(page, rect, baseline)]."""
        groups: dict[tuple, list] = {}
        order = []
        for p in range(s, min(e, len(u.chars))):
            c = u.chars[p]
            if c is None or not u.seg.text[p].strip():
                continue
            key = (c[0], c[1])
            if key not in groups:
                groups[key] = [fitz.Rect(c[2]), c[3]]
                order.append(key)
            else:
                groups[key][0] |= fitz.Rect(c[2])
        # Thai vowels/tone marks are zero-width boxes on the right edge; MuPDF treats an edge point
        # as outside, so nudge the right side (less than any real glyph's half-width).
        return [(k[0], fitz.Rect(r.x0, r.y0, r.x1 + 0.6, r.y1), b) for k in order for r, b in [groups[k]]]

    # ------------------------------------------------------------ outputs
    def render_highlight(self, spans):
        doc = _open(self.data)
        skipped = 0
        try:
            for u in self.units:
                sp = spans.get(u.seg.id)
                if not sp:
                    continue
                if u.ref[0] != "text":
                    skipped += len(sp)
                    continue
                by_page = defaultdict(list)
                for s, e in sp:
                    for pno, rect, _ in self._rects(u, s, e):
                        by_page[pno].append(rect)
                for pno, rects in by_page.items():
                    page = doc[pno]  # keep the page alive while its annotation is edited
                    a = page.add_highlight_annot(rects)
                    a.set_colors(stroke=(1, 0.93, 0.2))
                    a.update()
            out = doc.tobytes(garbage=3, deflate=True)
        finally:
            doc.close()
        return out, {"not_highlighted": skipped}

    def render_redacted(self, edits, sanitize=True):
        doc = _open(self.data)
        missed = moved = 0
        try:
            inserts: dict[int, list] = defaultdict(list)
            boxes: dict[int, list] = defaultdict(list)
            for u in self.units:
                ed = edits.get(u.seg.id)
                if not ed:
                    continue
                if u.ref[0] == "text":
                    for s, e, new in ed:
                        rects = self._rects(u, s, e)
                        if not rects:
                            missed += 1
                            continue
                        for i, (pno, rect, base) in enumerate(rects):
                            boxes[pno].append((rect, new == REMOVED))
                            if i == 0 and new != REMOVED:
                                inserts[pno].append((rect, base, new))
                else:
                    self._apply_other(doc, u, ed)
            for pno, items in boxes.items():
                page = doc[pno]
                prepare_page(doc, page)
                before = _char_sig(page)
                for rect, removed in items:
                    page.add_redact_annot(rect, fill=(0, 0, 0) if removed else (0.93, 0.93, 0.93), cross_out=False)
                page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_PIXELS, graphics=fitz.PDF_REDACT_LINE_ART_NONE)
                moved += moved_chars(before, page, [r for r, _ in items])
            for pno, items in inserts.items():
                page = doc[pno]
                for rect, base, new in items:
                    _draw_text(page, rect, base, new)
            if sanitize:
                m = doc.metadata or {}
                doc.set_metadata({"title": m.get("title") or "", "subject": m.get("subject") or "", "keywords": m.get("keywords") or "",
                                  "author": "", "creator": "", "producer": "PikaRedact"})
                try:
                    doc.scrub(attached_files=True, clean_pages=False, embedded_files=True, hidden_text=False, javascript=True,
                              metadata=False, redactions=False, remove_links=False, reset_fields=False, reset_responses=False,
                              thumbnails=True, xml_metadata=True)
                except Exception:
                    doc.del_xml_metadata()
            out = doc.tobytes(garbage=4, deflate=True, clean=True)
        finally:
            doc.close()
        info = {"missed": missed, "moved_chars": moved}
        if moved:
            info["warning"] = f"ข้อความรอบจุดที่ปิดใน PDF เลื่อนตำแหน่ง {moved} ตัวอักษร — เปิดตรวจไฟล์ก่อนส่งต่อ"
        return out, info

    def _apply_other(self, doc: fitz.Document, u: _PdfUnit, ed):
        from ..redact.operators import apply_edits

        new = apply_edits(u.seg.text, ed)
        kind = u.ref[0]
        if kind == "meta":
            m = dict(doc.metadata or {})
            m[u.ref[1]] = new
            doc.set_metadata({k: m.get(k) or "" for k in ("title", "author", "subject", "keywords", "creator", "producer")})
        elif kind == "annot":
            _, pno, xref, fld = u.ref
            page = doc[pno]
            for a in page.annots():
                if a.xref == xref:
                    a.set_info(**{fld: new})
                    a.update()
                    break
        elif kind == "widget":
            _, pno, xref = u.ref
            for w in doc[pno].widgets():
                if w.xref == xref:
                    w.field_value = new
                    w.update()
                    break
        elif kind == "toc":
            toc = doc.get_toc(simple=False)
            i = u.ref[1]
            if i < len(toc):
                toc[i][1] = new
                doc.set_toc(toc)
        elif kind == "link":
            _, pno, i = u.ref
            page = doc[pno]
            links = page.get_links()
            if i < len(links):
                page.delete_link(links[i])  # a link pointing at PII is dropped, its visible text is redacted separately


# ---------------------------------------------------------------- TJ workaround
# MuPDF (seen in 1.28.6) drops the advance of the first removed glyph when a redaction starts
# in the middle of a string inside a TJ array, so all text after it on the line slides left
# under the box. Tj strings are handled correctly, so before redacting we rewrite
# "[(ab) -50 (cd)] TJ" as "(ab) Tj [-50] TJ (cd) Tj" — same rendering, no font knowledge needed.
_WS = b" \t\r\n\x0c\x00"
_DELIM = b"()<>[]{}/%"
_NUM = re.compile(rb"[+-]?(?:\d+\.?\d*|\.\d+)\Z")


def _skip_ws(s: bytes, i: int) -> int:
    n = len(s)
    while i < n:
        c = s[i]
        if c in _WS:
            i += 1
        elif c == 0x25:  # % comment
            while i < n and s[i] not in b"\r\n":
                i += 1
        else:
            break
    return i


def _lit_end(s: bytes, i: int) -> int:
    """s[i] == '(' -> index after the matching ')'."""
    depth, n = 0, len(s)
    while i < n:
        c = s[i]
        if c == 0x5C:  # backslash escapes the next byte
            i += 2
            continue
        if c == 0x28:
            depth += 1
        elif c == 0x29:
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return n


def _regular_end(s: bytes, i: int) -> int:
    n = len(s)
    while i < n and s[i] not in _WS and s[i] not in _DELIM:
        i += 1
    return i


def _parse_tj_array(s: bytes, i: int):
    """s[i] == '[' -> (end, [(is_string, raw)]) for an array of strings/numbers only, else (None, None)."""
    items, i, n = [], i + 1, len(s)
    while True:
        i = _skip_ws(s, i)
        if i >= n:
            return None, None
        c = s[i]
        if c == 0x5D:  # ]
            return i + 1, items
        if c == 0x28:
            j = _lit_end(s, i)
            items.append((True, s[i:j]))
        elif c == 0x3C and s[i + 1:i + 2] != b"<":
            j = s.find(b">", i)
            if j < 0:
                return None, None
            j += 1
            items.append((True, s[i:j]))
        else:
            j = _regular_end(s, i)
            if j == i or not _NUM.match(s[i:j]):
                return None, None
            items.append((False, s[i:j]))
        i = j


def split_tj(s: bytes) -> bytes | None:
    """Rewrite TJ arrays as Tj + spacing-only TJ. Returns None when nothing changed."""
    out, last, i, n, changed = bytearray(), 0, 0, len(s), False
    while True:
        i = _skip_ws(s, i)
        if i >= n:
            break
        c = s[i]
        if c == 0x28:
            i = _lit_end(s, i)
        elif c == 0x3C:
            if s[i + 1:i + 2] == b"<":
                i += 2
            else:
                j = s.find(b">", i)
                i = n if j < 0 else j + 1
        elif c == 0x3E:
            i += 2 if s[i + 1:i + 2] == b">" else 1
        elif c == 0x5B:
            end, items = _parse_tj_array(s, i)
            if items is None:
                i += 1
                continue
            k = _skip_ws(s, end)
            if s[k:k + 2] == b"TJ" and (k + 2 >= n or s[k + 2] in _WS or s[k + 2] in _DELIM):
                parts, nums = [], []
                for is_str, raw in items:
                    if is_str:
                        if nums:
                            parts.append(b"[" + b" ".join(nums) + b"] TJ")
                            nums = []
                        parts.append(raw + b" Tj")
                    else:
                        nums.append(raw)
                if nums:
                    parts.append(b"[" + b" ".join(nums) + b"] TJ")
                out += s[last:i] + b" " + b" ".join(parts) + b" "
                last = i = k + 2
                changed = True
            else:
                i = end
        elif c in b"]{}":
            i += 1
        elif c == 0x2F:  # /Name
            i = _regular_end(s, i + 1)
        else:
            j = _regular_end(s, i)
            tok = s[i:j]
            i = max(j, i + 1)
            if tok == b"ID":  # inline image: binary data up to a whitespace-delimited EI
                m = re.compile(rb"[\s]EI(?=[\s]|\Z)").search(s, i + 1)
                i = n if m is None else m.end()
    if not changed:
        return None
    out += s[last:]
    return bytes(out)


def _char_sig(page: fitz.Page) -> list[tuple]:
    raw = page.get_text("rawdict", flags=_FLAGS)
    return [(ch["c"], round(ch["bbox"][0], 1), round(ch["bbox"][1], 1))
            for b in raw.get("blocks", []) if b.get("type") == 0
            for ln in b["lines"] for sp in ln["spans"] for ch in sp["chars"]]


def _content_xrefs(doc: fitz.Document, page: fitz.Page) -> list[int]:
    xrefs = list(page.get_contents())
    for item in page.get_xobjects():
        x = item[0]
        if x and doc.xref_get_key(x, "Subtype")[1] == "/Form" and x not in xrefs:
            xrefs.append(x)
    return xrefs


def prepare_page(doc: fitz.Document, page: fitz.Page) -> bool:
    """Apply the TJ rewrite to a page about to be redacted; revert if the text moved. True if rewritten."""
    before = _char_sig(page)
    saved = {}
    for x in _content_xrefs(doc, page):
        try:
            data = doc.xref_stream(x)
            new = split_tj(data) if data else None
        except Exception:
            new = None
        if new is not None:
            saved[x] = data
            doc.update_stream(x, new)
    if not saved:
        return False
    if _char_sig(page) != before:
        for x, data in saved.items():
            doc.update_stream(x, data)
        return False
    return True


def moved_chars(before: list[tuple], page: fitz.Page, rects: list[fitz.Rect]) -> int:
    """Characters outside every redaction box that are no longer where they were."""
    now = {}
    for c, x, y in _char_sig(page):
        now.setdefault(c, []).append((x, y))
    lost = 0
    for c, x, y in before:
        if not c.strip():
            continue
        p = fitz.Point(x + 0.5, y + 0.5)
        if any(fitz.Rect(r.x0 - 1, r.y0 - 1, r.x1 + 1, r.y1 + 1).contains(p) for r in rects):
            continue
        if not any(abs(x - x2) <= 0.6 and abs(y - y2) <= 0.6 for x2, y2 in now.get(c, ())):
            lost += 1
    return lost


_FONT_CACHE: dict[str, fitz.Font] = {}


def _font() -> fitz.Font | None:
    path = config.PDF_THAI_FONT
    if not path:
        return None
    if path not in _FONT_CACHE:
        _FONT_CACHE[path] = fitz.Font(fontfile=path)
    return _FONT_CACHE[path]


def _draw_text(page: fitz.Page, rect: fitz.Rect, base: float, text: str):
    """Draw the replacement inside the redacted box (Thai-capable font, shrunk to fit)."""
    font = _font()
    h = max(rect.height, 4)
    size = h * 0.78
    if font is not None:
        need = font.text_length(text, fontsize=1) or 1
        size = max(3.5, min(size, (rect.width + h * 0.6) / need))
        # HTML box gives proper Thai shaping (combining vowels/tone marks)
        try:
            arch = fitz.Archive(os.path.dirname(config.PDF_THAI_FONT))
            fname = os.path.basename(config.PDF_THAI_FONT)
            css = (f"@font-face {{font-family: th; src: url('{fname}');}} "
                   f"* {{font-family: th; font-size: {size:.2f}px; line-height: 1; color: #333; margin: 0; padding: 0; white-space: nowrap;}}")
            box = fitz.Rect(rect.x0, rect.y0 - h * 0.15, rect.x1 + h * 0.6 + rect.width, rect.y1 + h * 0.25)
            html = "<span>" + text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;") + "</span>"
            spare, _ = page.insert_htmlbox(box, html, css=css, archive=arch, scale_low=0.5)
            if spare >= 0:
                return
        except Exception:
            pass
        page.insert_font(fontname="pikath", fontfile=config.PDF_THAI_FONT)
        page.insert_text((rect.x0, base), text, fontname="pikath", fontsize=size, color=(0.2, 0.2, 0.2))
    else:
        page.insert_text((rect.x0, base), text.encode("ascii", "replace").decode(), fontname="helv", fontsize=size, color=(0.2, 0.2, 0.2))
