"""DOCX / XLSX / PPTX via zip + lxml (no Office needed).

Covers body, tables, text boxes, headers/footers, foot/endnotes, comments and
their authors, tracked deletions, field codes, speaker notes, slide masters,
charts, SmartArt, hyperlinks, document properties, pivot caches and embedded
Office files (recursively). Each render re-parses the original bytes, so
highlight and redact outputs can both be made from one scan.
"""
from __future__ import annotations

import copy
import io
import posixpath
import re
import zipfile
from collections import defaultdict

from lxml import etree

from ..models import Segment
from .base import Document, Holder, Unit

NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "c": "http://schemas.openxmlformats.org/drawingml/2006/chart",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "pr": "http://schemas.openxmlformats.org/package/2006/relationships",
    "ct": "http://schemas.openxmlformats.org/package/2006/content-types",
    "cp": "http://schemas.openxmlformats.org/package/2006/metadata/core-properties",
    "dc": "http://purl.org/dc/elements/1.1/",
    "ep": "http://schemas.openxmlformats.org/officeDocument/2006/extended-properties",
    "vt": "http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes",
    "w15": "http://schemas.microsoft.com/office/word/2012/wordml",
    "p188": "http://schemas.microsoft.com/office/powerpoint/2018/8/main",
    "xtc": "http://schemas.microsoft.com/office/spreadsheetml/2018/threadedcomments",
}
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"


def q(prefix: str, name: str) -> str:
    return f"{{{NS[prefix]}}}{name}"


W_P, W_T, W_DT, W_R, W_RPR, W_INSTR = q("w", "p"), q("w", "t"), q("w", "delText"), q("w", "r"), q("w", "rPr"), q("w", "instrText")
W_TAB, W_BR, W_CR, W_TC, W_TR, W_TBL, W_GRIDSPAN, W_TCPR = (q("w", n) for n in ("tab", "br", "cr", "tc", "tr", "tbl", "gridSpan", "tcPr"))
W_NBH = q("w", "noBreakHyphen")
A_P, A_T, A_R, A_RPR, A_BR, A_TC, A_TR, A_TBL, A_FLD = (q("a", n) for n in ("p", "t", "r", "rPr", "br", "tc", "tr", "tbl", "fld"))

MAX_TOTAL_BYTES = 800 * 1024 * 1024
MAX_ENTRIES = 20000
MAX_DEPTH = 2
_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=True, remove_blank_text=False)
_OFFICE_EXT = (".docx", ".docm", ".xlsx", ".xlsm", ".pptx", ".pptm")
_IMAGE_EXT = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".emf", ".wmf", ".svg", ".webp")

# Children that must come *after* the highlight element (schema order).
_W_RPR_AFTER = {"u", "effect", "bdr", "shd", "fitText", "vertAlign", "rtl", "cs", "em", "lang", "eastAsianLayout", "specVanish", "oMath", "rPrChange"}
_A_RPR_AFTER = {"uLnTx", "uLn", "uFillTx", "uFill", "latin", "ea", "cs", "sym", "hlinkClick", "hlinkMouseOver", "rtl", "extLst"}
HL_RGB = "FFFF00"


def _local(tag) -> str:
    return tag.split("}", 1)[1] if isinstance(tag, str) and "}" in tag else str(tag)


def _num(name: str) -> int:
    m = re.search(r"(\d+)\.xml$", name)
    return int(m.group(1)) if m else 0


def kind_of(name: str) -> str | None:
    ext = posixpath.splitext(name.lower())[1]
    return {".docx": "docx", ".docm": "docx", ".xlsx": "xlsx", ".xlsm": "xlsx", ".pptx": "pptx", ".pptm": "pptx"}.get(ext)


def _col_index(ref: str) -> tuple[int, int]:
    m = re.match(r"([A-Z]+)(\d+)", ref or "")
    if not m:
        return 0, 0
    col = 0
    for ch in m.group(1):
        col = col * 26 + (ord(ch) - 64)
    return int(m.group(2)), col


class Package:
    """One OOXML zip. Builds Units in a deterministic order, so ids match across re-parses."""

    def __init__(self, data: bytes, kind: str, prefix: str = "", group_prefix: str = "", depth: int = 0):
        self.data, self.kind, self.prefix, self.gp, self.depth = data, kind, prefix, group_prefix, depth
        self.zf = zipfile.ZipFile(io.BytesIO(data))
        infos = self.zf.infolist()
        if len(infos) > MAX_ENTRIES or sum(i.file_size for i in infos) > MAX_TOTAL_BYTES:
            raise ValueError("ไฟล์ใหญ่หรือมีส่วนประกอบมากเกินไป")
        self.infos = infos
        self.names = [i.filename for i in infos]
        self.trees: dict[str, etree._Element] = {}
        self.units: list[Unit] = []
        self.children: dict[str, "Package"] = {}
        self.warnings: list[str] = []
        self.n = 0
        self.tables: dict[int, int] = {}
        self._build()

    # ------------------------------------------------------------ helpers
    def tree(self, name: str):
        if name not in self.trees:
            self.trees[name] = etree.fromstring(self.zf.read(name), _PARSER)
        return self.trees[name]

    def unit(self, label: str, group: str, kind: str, hl: str | None = None, cell=None) -> Unit:
        self.n += 1
        seg = Segment(f"{self.prefix}s{self.n}", "", label, (self.gp + group) if self.gp else group, kind, cell)
        return Unit(seg, hl=hl)

    def keep(self, u: Unit) -> None:
        if u.seg.text.strip():
            self.units.append(u)

    def rels(self, part: str) -> dict[str, str]:
        d, b = posixpath.split(part)
        rp = posixpath.join(d, "_rels", b + ".rels")
        if rp not in self.names:
            return {}
        out = {}
        for rel in self.tree(rp).iter(q("pr", "Relationship")):
            if rel.get("TargetMode") == "External":
                continue
            tgt = rel.get("Target", "")
            # targets are relative to the part, or absolute from the package root ("/xl/worksheets/sheet1.xml")
            out[rel.get("Id")] = posixpath.normpath(tgt.lstrip("/") if tgt.startswith("/") else posixpath.join(d, tgt))
        return out

    def _table_pos(self, p, tc_tag, tr_tag, tbl_tag, gridspan=False):
        tc = next((a for a in p.iterancestors() if a.tag in (tc_tag, W_P, A_P) and a is not p), None)
        if tc is None or tc.tag != tc_tag:
            return None
        tr = tc.getparent()
        tbl = tr.getparent() if tr is not None else None
        if tr is None or tbl is None or tr.tag != tr_tag or tbl.tag != tbl_tag:
            return None
        key = tbl  # keeping the proxy alive keeps its identity stable
        if key not in self.tables:
            self.tables[key] = len(self.tables) + 1
        row = [c for c in tbl if c.tag == tr_tag].index(tr)
        col = 0
        for c in tr:
            if c is tc:
                break
            if c.tag == tc_tag:
                span = 1
                if gridspan:
                    gs = c.find(f"{W_TCPR}/{W_GRIDSPAN}")
                    if gs is not None and (gs.get(q("w", "val")) or "1").isdigit():
                        span = int(gs.get(q("w", "val")))
                col += span
        return self.tables[key], row, col

    # ------------------------------------------------------------ paragraph walkers
    def _w_paragraphs(self, part: str, group: str, kind: str, label_fn):
        root = self.tree(part)
        n_body = 0
        for p in root.iter(W_P):
            u_field = None
            tpos = self._table_pos(p, W_TC, W_TR, W_TBL, gridspan=True)
            if tpos:
                t, r, c = tpos
                u = self.unit(f"{label_fn('table')} ตาราง {t} แถว {r + 1} คอลัมน์ {c + 1}".strip(), group, kind, "w",
                              (f"docx:{self.prefix}{part}:{t}", r, c))
            else:
                n_body += 1
                u = self.unit(label_fn(n_body), group, kind, "w")
            stack = [iter(p)]
            while stack:
                el = next(stack[-1], None)
                if el is None:
                    stack.pop()
                    continue
                tag = el.tag
                if tag == W_P:
                    continue  # nested paragraph (text box) is visited on its own
                if tag in (W_T, W_DT):
                    u.add(Holder(el, preserve=XML_SPACE))
                elif tag == W_INSTR:
                    if u_field is None:
                        u_field = self.unit(f"{u.seg.label} · โค้ดฟิลด์", group, "field")
                    u_field.add(Holder(el, preserve=XML_SPACE))
                    u_field.virtual(" ")
                elif tag in (W_TAB,):
                    u.virtual("\t")
                elif tag in (W_BR, W_CR):
                    u.virtual(" ")
                elif tag == W_NBH:
                    u.virtual("-")
                elif tag == q("w", "fldSimple") and el.get(q("w", "instr")):
                    if u_field is None:
                        u_field = self.unit(f"{u.seg.label} · โค้ดฟิลด์", group, "field")
                    u_field.add(Holder(el, q("w", "instr")))
                    u_field.virtual(" ")
                    stack.append(iter(el))
                elif len(el):
                    stack.append(iter(el))
            self.keep(u)
            if u_field is not None:
                self.keep(u_field)

    def _a_paragraphs(self, part: str, group: str, kind: str, label: str, table_prefix: str):
        root = self.tree(part)
        for p in root.iter(A_P):
            tpos = self._table_pos(p, A_TC, A_TR, A_TBL)
            if tpos:
                t, r, c = tpos
                u = self.unit(f"{label} · ตาราง {t} แถว {r + 1} คอลัมน์ {c + 1}", group, kind, "a", (f"{self.kind}:{table_prefix}:{t}", r, c))
            else:
                u = self.unit(label, group, kind, "a")
            for el in p.iter(A_T, A_BR):
                if el.tag == A_T:
                    u.add(Holder(el))
                else:
                    u.virtual(" ")
            self.keep(u)

    def _attrs(self, part: str, tag: str, attrs: list[str], label: str, group: str, kind="metadata"):
        if part not in self.names:
            return
        for el in self.tree(part).iter(tag):
            for a in attrs:
                if el.get(a):
                    u = self.unit(label, group, kind)
                    u.add(Holder(el, a))
                    self.keep(u)

    # ------------------------------------------------------------ common parts
    def _common(self):
        names = self.names
        # document properties
        if "docProps/core.xml" in names:
            for el in self.tree("docProps/core.xml"):
                if el.text and el.text.strip() and _local(el.tag) not in ("created", "modified", "revision", "lastPrinted"):
                    u = self.unit(f"ข้อมูลไฟล์ · {_local(el.tag)}", "ข้อมูลไฟล์", "metadata")
                    u.add(Holder(el))
                    self.keep(u)
        if "docProps/app.xml" in names:
            for el in self.tree("docProps/app.xml"):
                if _local(el.tag) in ("Company", "Manager", "HyperlinkBase") and el.text and el.text.strip():
                    u = self.unit(f"ข้อมูลไฟล์ · {_local(el.tag)}", "ข้อมูลไฟล์", "metadata")
                    u.add(Holder(el))
                    self.keep(u)
        if "docProps/custom.xml" in names:
            for el in self.tree("docProps/custom.xml").iter(q("vt", "lpwstr"), q("vt", "lpstr")):
                if el.text and el.text.strip():
                    u = self.unit("ข้อมูลไฟล์ · custom", "ข้อมูลไฟล์", "metadata")
                    u.add(Holder(el))
                    self.keep(u)
        for name in names:
            low = name.lower()
            if re.search(r"/charts/chart\d+\.xml$", low):
                g = "แผนภูมิ"
                root = self.tree(name)
                for v in root.iter(q("c", "v")):
                    if v.text and re.search(r"[A-Za-z\u0e00-\u0e7f]", v.text):
                        u = self.unit(f"แผนภูมิ {_num(name)}", g, "chart")
                        u.add(Holder(v))
                        self.keep(u)
                self._a_paragraphs(name, g, "chart", f"แผนภูมิ {_num(name)} · ข้อความ", f"{self.prefix}{name}")
            elif re.search(r"/diagrams/(data|drawing)\d+\.xml$", low):
                self._a_paragraphs(name, "SmartArt", "body", f"SmartArt {_num(name)}", f"{self.prefix}{name}")
            elif low.endswith(".rels"):
                for rel in self.tree(name).iter(q("pr", "Relationship")):
                    tgt = rel.get("Target", "")
                    if rel.get("TargetMode") == "External" and tgt:
                        u = self.unit("ลิงก์", "ลิงก์ในเอกสาร", "link")
                        u.add(Holder(rel, "Target"))
                        self.keep(u)
        n_img = sum(1 for n in names if n.lower().endswith(_IMAGE_EXT) and "/media/" in n.lower())
        if n_img:
            self.warnings.append(f"{self.gp}รูปภาพ {n_img} รูปยังไม่ได้ตรวจ — ข้อความในรูปรองรับใน Phase 2 เปิดดูก่อนส่งต่อ")
        if any(n.lower().endswith(".bin") and "embeddings/" in n.lower() for n in names):
            self.warnings.append(f"{self.gp}มีไฟล์ฝังชนิด OLE (.bin) ที่ยังตรวจไม่ได้")
        if any(n.lower().endswith("vbaproject.bin") for n in names):
            self.warnings.append(f"{self.gp}ไฟล์มีมาโคร (VBA) — โค้ดมาโครไม่ได้ถูกตรวจ")
        if self.depth < MAX_DEPTH:
            k = 0
            for name in names:
                if "embeddings/" in name.lower() and name.lower().endswith(_OFFICE_EXT):
                    k += 1
                    try:
                        child = Package(self.zf.read(name), kind_of(name), f"{self.prefix}e{k}.", f"{self.gp}ไฟล์ฝัง {posixpath.basename(name)} · ", self.depth + 1)
                    except Exception:
                        self.warnings.append(f"เปิดไฟล์ฝัง {posixpath.basename(name)} ไม่ได้")
                        continue
                    self.children[name] = child
                    self.units.extend(child.units)
                    self.warnings.extend(child.warnings)

    # ------------------------------------------------------------ per format
    def _build(self):
        names = self.names
        if self.kind == "docx":
            main = [n for n in names if re.fullmatch(r"word/(glossary/)?document\d*\.xml", n)]
            for n in main:
                grp = "เนื้อหา" if "glossary" not in n else "Building blocks"
                self._w_paragraphs(n, grp, "body", lambda k: "" if k == "table" else f"ย่อหน้า {k}")
            for n in sorted(names, key=_num):
                if re.fullmatch(r"word/header\d*\.xml", n):
                    self._w_paragraphs(n, "หัวกระดาษ", "header", lambda k: "หัวกระดาษ" if k == "table" else "หัวกระดาษ")
                elif re.fullmatch(r"word/footer\d*\.xml", n):
                    self._w_paragraphs(n, "ท้ายกระดาษ", "footer", lambda k: "ท้ายกระดาษ" if k == "table" else "ท้ายกระดาษ")
            for n, g in (("word/footnotes.xml", "เชิงอรรถ"), ("word/endnotes.xml", "อ้างอิงท้ายเรื่อง")):
                if n in names:
                    self._w_paragraphs(n, g, "note", lambda k, g=g: g)
            if "word/comments.xml" in names:
                root = self.tree("word/comments.xml")
                for i, cm in enumerate(root.iter(q("w", "comment")), 1):
                    for a, lab in ((q("w", "author"), "ผู้เขียนคอมเมนต์"), (q("w", "initials"), "อักษรย่อผู้เขียน")):
                        if cm.get(a):
                            u = self.unit(f"คอมเมนต์ {i} · {lab}", "คอมเมนต์", "metadata")
                            u.add(Holder(cm, a))
                            self.keep(u)
                self._w_paragraphs("word/comments.xml", "คอมเมนต์", "comment", lambda k: "คอมเมนต์")
            self._attrs("word/people.xml", q("w15", "person"), [q("w15", "author")], "ผู้แก้ไขเอกสาร", "ข้อมูลไฟล์")
            # tracked-change authors sit on many elements; they are anonymised by sanitize
            authors = set()
            for n in main:
                for el in self.tree(n).iter(q("w", "ins"), q("w", "del"), q("w", "moveFrom"), q("w", "moveTo"), q("w", "rPrChange"), q("w", "pPrChange")):
                    if el.get(q("w", "author")):
                        authors.add(el.get(q("w", "author")))
            if authors:
                self.warnings.append(f"{self.gp}มีประวัติแก้ไข (Track changes) จากผู้แก้ไข {len(authors)} คน — ข้อความที่ถูกลบจะถูกตรวจด้วย และชื่อผู้แก้ไขจะถูกแทนด้วย \"ผู้ตรวจ\" เมื่อเลือกล้างข้อมูลไฟล์")
            self._common()
        elif self.kind == "pptx":
            slides = sorted([n for n in names if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)], key=_num)
            order = self._slide_order() or slides
            slide_no = {n: i for i, n in enumerate(order, 1)}
            for n in order:
                if n in names:
                    k = slide_no[n]
                    self._a_paragraphs(n, f"สไลด์ {k}", "body", f"สไลด์ {k}", f"{self.prefix}{n}")
            for n in sorted([n for n in names if re.fullmatch(r"ppt/notesSlides/notesSlide\d+\.xml", n)], key=_num):
                tgt = next((t for t in self.rels(n).values() if "/slides/" in t), None)
                k = slide_no.get(tgt, _num(n))
                self._a_paragraphs(n, f"สไลด์ {k}", "note", f"โน้ตผู้บรรยาย สไลด์ {k}", f"{self.prefix}{n}")
            for n in sorted(names):
                if re.fullmatch(r"ppt/(slideLayouts|slideMasters|notesMasters|handoutMasters)/\w+\.xml", n):
                    self._a_paragraphs(n, "แม่แบบสไลด์", "hidden", "แม่แบบสไลด์", f"{self.prefix}{n}")
            for n in sorted(names):
                if re.fullmatch(r"ppt/comments/comment\d+\.xml", n):
                    for t in self.tree(n).iter(q("p", "text")):
                        u = self.unit("คอมเมนต์", "คอมเมนต์", "comment")
                        u.add(Holder(t))
                        self.keep(u)
                elif re.fullmatch(r"ppt/comments/modernComment[^/]*\.xml", n):
                    self._a_paragraphs(n, "คอมเมนต์", "comment", "คอมเมนต์", f"{self.prefix}{n}")
            self._attrs("ppt/commentAuthors.xml", q("p", "cmAuthor"), ["name", "initials"], "ผู้เขียนคอมเมนต์", "คอมเมนต์")
            self._attrs("ppt/authors.xml", q("p188", "author"), ["name", "initials", "userId"], "ผู้เขียนคอมเมนต์", "คอมเมนต์")
            self._common()
        elif self.kind == "xlsx":
            self._xlsx()
            for n in sorted(names, key=_num):
                if re.fullmatch(r"xl/drawings/drawing\d+\.xml", n):
                    self._a_paragraphs(n, "กล่องข้อความในชีต", "body", f"กล่องข้อความ {_num(n)}", f"{self.prefix}{n}")
                elif re.fullmatch(r"xl/comments\d*\.xml", n):
                    root = self.tree(n)
                    for a in root.iter(q("s", "author")):
                        u = self.unit("ผู้เขียนคอมเมนต์", "คอมเมนต์", "metadata")
                        u.add(Holder(a))
                        self.keep(u)
                    for cm in root.iter(q("s", "comment")):
                        u = self.unit(f"คอมเมนต์ {cm.get('ref', '')}", "คอมเมนต์", "comment")
                        for t in cm.iter(q("s", "t")):
                            u.add(Holder(t, preserve=XML_SPACE))
                        self.keep(u)
                elif re.fullmatch(r"xl/threadedComments/[^/]+\.xml", n):
                    for t in self.tree(n).iter(q("xtc", "text")):
                        u = self.unit("คอมเมนต์", "คอมเมนต์", "comment")
                        u.add(Holder(t))
                        self.keep(u)
                elif re.fullmatch(r"xl/persons/[^/]+\.xml", n):
                    self._attrs(n, q("xtc", "person"), ["displayName", "userId"], "ผู้เขียนคอมเมนต์", "คอมเมนต์")
                elif re.fullmatch(r"xl/pivotCache/pivotCache(Definition|Records)\d+\.xml", n):
                    for el in self.tree(n).iter(q("s", "s")):
                        if el.get("v"):
                            u = self.unit("แคช Pivot", "Pivot cache", "hidden")
                            u.add(Holder(el, "v"))
                            self.keep(u)
            self._common()
        else:
            raise ValueError(f"unsupported {self.kind}")

    def _slide_order(self) -> list[str]:
        if "ppt/presentation.xml" not in self.names:
            return []
        rels = self.rels("ppt/presentation.xml")
        out = []
        for sid in self.tree("ppt/presentation.xml").iter(q("p", "sldId")):
            t = rels.get(sid.get(q("r", "id")))
            if t:
                out.append(t)
        return out

    def _xlsx(self):
        names = self.names
        wb = "xl/workbook.xml"
        if wb not in names:
            return
        rels = self.rels(wb)
        sheets = []
        for sh in self.tree(wb).iter(q("s", "sheet")):
            t = rels.get(sh.get(q("r", "id")))
            if t and t in names:
                sheets.append((sh.get("name", "Sheet"), t, sh.get("state", "visible")))
        sst = "xl/sharedStrings.xml"
        si_list = list(self.tree(sst).iter(q("s", "si"))) if sst in names else []
        si_unit: dict[int, Unit] = {}
        for name, part, state in sheets:
            kind = "hidden" if state != "visible" else "table"
            group = f"ชีต {name}"
            root = self.tree(part)
            for c in root.iter(q("s", "c")):
                ref = c.get("r", "")
                row, col = _col_index(ref)
                key = (f"xlsx:{self.prefix}{name}", row, col)
                t = c.get("t", "n")
                v = c.find(q("s", "v"))
                if t == "s" and v is not None and (v.text or "").strip().isdigit():
                    idx = int(v.text)
                    if idx >= len(si_list):
                        continue
                    if idx not in si_unit:
                        u = self.unit(f"ชีต {name} · {ref}", group, kind, "cell", [key])
                        for tt in si_list[idx].iter(q("s", "t")):
                            if tt.getparent().tag != q("s", "rPh"):
                                u.add(Holder(tt, preserve=XML_SPACE))
                        si_unit[idx] = u
                        self.keep(u)
                    else:
                        si_unit[idx].seg.cell.append(key)
                    si_unit[idx].cells.append(c)
                elif t == "inlineStr":
                    u = self.unit(f"ชีต {name} · {ref}", group, kind, "cell", [key])
                    for tt in c.iter(q("s", "t")):
                        u.add(Holder(tt, preserve=XML_SPACE))
                    u.cells.append(c)
                    self.keep(u)
                elif t == "str" and v is not None:
                    u = self.unit(f"ชีต {name} · {ref}", group, kind, "cell", [key])
                    u.add(Holder(v))
                    u.cells.append(c)
                    self.keep(u)
                elif t == "n" and v is not None and re.fullmatch(r"\d{4,19}", (v.text or "").strip()):
                    u = self.unit(f"ชีต {name} · {ref}", group, kind, "cell", [key])
                    u.add(Holder(v))
                    u.cells.append(c)
                    u.numeric = True
                    self.keep(u)
            for hf in root.iter(*(q("s", n) for n in ("oddHeader", "oddFooter", "evenHeader", "evenFooter", "firstHeader", "firstFooter"))):
                if hf.text and hf.text.strip():
                    u = self.unit(f"ชีต {name} · หัว/ท้ายกระดาษ", group, "header")
                    u.add(Holder(hf))
                    self.keep(u)
        for idx, si in enumerate(si_list):
            if idx not in si_unit:
                u = self.unit("ข้อความที่ไม่ได้ใช้ในเซลล์", "ข้อความที่ซ่อนอยู่", "hidden")
                for tt in si.iter(q("s", "t")):
                    u.add(Holder(tt, preserve=XML_SPACE))
                self.keep(u)

    # ------------------------------------------------------------ output
    def serialize(self, drop: set[str] | None = None, replace: dict[str, bytes] | None = None) -> bytes:
        drop, replace = drop or set(), replace or {}
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            for info in self.infos:
                name = info.filename
                if name in drop:
                    continue
                if name in replace:
                    data = replace[name]
                elif name in self.trees:
                    data = etree.tostring(self.trees[name], xml_declaration=True, encoding="UTF-8", standalone=True)
                elif name in self.children:
                    data = self.children[name].data
                else:
                    data = self.zf.read(name)
                zi = zipfile.ZipInfo(name, date_time=info.date_time)
                zi.compress_type = zipfile.ZIP_STORED if info.compress_type == zipfile.ZIP_STORED else zipfile.ZIP_DEFLATED
                zi.external_attr = info.external_attr
                z.writestr(zi, data)
        return out.getvalue()

    def sanitize(self) -> set[str]:
        """Clear author/company fields and drop the page thumbnail. Returns parts to drop."""
        drop: set[str] = set()
        if "docProps/core.xml" in self.names:
            for el in self.tree("docProps/core.xml"):
                if _local(el.tag) in ("creator", "lastModifiedBy"):
                    el.text = ""
        if "docProps/app.xml" in self.names:
            for el in self.tree("docProps/app.xml"):
                if _local(el.tag) in ("Company", "Manager"):
                    el.text = ""
        anon = "ผู้ตรวจ"
        for name in self.names:
            if not name.endswith(".xml") or "/media/" in name:
                continue
            if self.kind == "docx" and name.startswith("word/"):
                root = self.tree(name)
                for el in root.iter():
                    if el.get(q("w", "author")):
                        el.set(q("w", "author"), anon)
                    if el.get(q("w", "initials")):
                        el.set(q("w", "initials"), "R")
                    if el.get(q("w15", "author")):
                        el.set(q("w15", "author"), anon)
                for pr in root.iter(q("w15", "presenceInfo")):
                    pr.getparent().remove(pr)
            elif name in ("ppt/commentAuthors.xml", "ppt/authors.xml"):
                for el in self.tree(name).iter():
                    for a in ("name", "initials", "userId"):
                        if el.get(a):
                            el.set(a, {"name": anon, "initials": "R", "userId": anon}[a])
            elif re.fullmatch(r"xl/comments\d*\.xml", name):
                for a in self.tree(name).iter(q("s", "author")):
                    a.text = anon
            elif re.fullmatch(r"xl/persons/[^/]+\.xml", name):
                for el in self.tree(name).iter(q("xtc", "person")):
                    el.set("displayName", anon)
                    if el.get("userId"):
                        el.set("userId", anon)
        thumbs = [n for n in self.names if n.lower().startswith("docprops/thumbnail")]
        if thumbs:
            drop |= set(thumbs)
            if "_rels/.rels" in self.names:
                root = self.tree("_rels/.rels")
                for rel in list(root):
                    if rel.get("Target", "").lstrip("/").lower().startswith("docprops/thumbnail"):
                        root.remove(rel)
            if "[Content_Types].xml" in self.names:
                root = self.tree("[Content_Types].xml")
                for ov in list(root.iter(q("ct", "Override"))):
                    if ov.get("PartName", "").lower().startswith("/docprops/thumbnail"):
                        root.remove(ov)
        return drop

    def redact(self, edits: dict[str, list[tuple[int, int, str]]], sanitize: bool) -> tuple[bytes, int]:
        missed = 0
        for u in self.units:
            if u.seg.id.startswith(self.prefix) and not any(u.seg.id.startswith(k.prefix) for k in self.children.values()):
                e = edits.get(u.seg.id)
                if e:
                    missed += u.apply(e)
                    if u.numeric:
                        _numeric_to_text(u)
        for name, child in self.children.items():
            data, m = child.redact(edits, sanitize)
            child.data = data
            missed += m
        drop = self.sanitize() if sanitize else set()
        return self.serialize(drop), missed

    def highlight(self, spans: dict[str, list[tuple[int, int]]]) -> tuple[bytes, int]:
        skipped = 0
        w_runs: dict[int, tuple] = {}
        a_runs: dict[int, tuple] = {}
        cell_units: list[Unit] = []
        for u in self.units:
            sp = spans.get(u.seg.id)
            if not sp or any(u.seg.id.startswith(k.prefix) for k in self.children.values()):
                continue
            if u.hl == "cell":
                cell_units.append(u)
                continue
            if u.hl not in ("w", "a"):
                skipped += 1
                continue
            for hi, offs in u.marks_by_holder(sp).items():
                el = u.holders[hi].el
                run = el.getparent()
                if run is None or run.tag not in (W_R, A_R):
                    skipped += 1
                    continue
                book = w_runs if run.tag == W_R else a_runs
                book.setdefault(id(run), (run, {}))[1].setdefault(id(el), set()).update(offs)
        for run, marks in w_runs.values():
            _split_run(run, marks, W_RPR, _w_hl, (W_T, W_DT))
        for run, marks in a_runs.values():
            _split_run(run, marks, A_RPR, _a_hl, (A_T,))
        if cell_units:
            self._fill_cells(cell_units)
        for name, child in self.children.items():
            data, s = child.highlight(spans)
            child.data = data
            skipped += s
        return self.serialize(), skipped

    def _fill_cells(self, units: list[Unit]):
        st = "xl/styles.xml"
        if st not in self.names:
            return
        root = self.tree(st)
        fills = root.find(q("s", "fills"))
        xfs = root.find(q("s", "cellXfs"))
        if fills is None or xfs is None:
            return
        fill = etree.SubElement(fills, q("s", "fill"))
        pf = etree.SubElement(fill, q("s", "patternFill"), patternType="solid")
        etree.SubElement(pf, q("s", "fgColor"), rgb="FF" + HL_RGB)
        etree.SubElement(pf, q("s", "bgColor"), indexed="64")
        fills.set("count", str(len(fills)))
        fill_id = len(fills) - 1
        xf_list = list(xfs)
        new_of: dict[int, int] = {}
        for u in units:
            for c in u.cells:
                s = int(c.get("s", "0") or 0)
                if s not in new_of:
                    base = copy.deepcopy(xf_list[s]) if s < len(xf_list) else etree.Element(q("s", "xf"), numFmtId="0", fontId="0", borderId="0", xfId="0")
                    base.set("fillId", str(fill_id))
                    base.set("applyFill", "1")
                    xfs.append(base)
                    new_of[s] = len(xfs) - 1
                c.set("s", str(new_of[s]))
        xfs.set("count", str(len(xfs)))


def _numeric_to_text(u: Unit):
    """A number cell whose value was replaced by text must become a string cell."""
    for c in u.cells:
        v = c.find(q("s", "v"))
        if v is None:
            continue
        val = v.text or ""
        if re.fullmatch(r"-?\d+(\.\d+)?", val):
            continue
        if c.find(q("s", "f")) is not None:
            c.set("t", "str")
            continue
        c.remove(v)
        c.set("t", "inlineStr")
        is_ = etree.SubElement(c, q("s", "is"))
        t = etree.SubElement(is_, q("s", "t"))
        t.text = val
        t.set(XML_SPACE, "preserve")


def _insert_ordered(parent, el, after_names: set[str]):
    for i, ch in enumerate(parent):
        if _local(ch.tag) in after_names:
            parent.insert(i, el)
            return
    parent.append(el)


def _w_hl(rpr):
    for h in rpr.findall(q("w", "highlight")):
        rpr.remove(h)
    el = etree.Element(q("w", "highlight"))
    el.set(q("w", "val"), "yellow")
    _insert_ordered(rpr, el, _W_RPR_AFTER)


def _a_hl(rpr):
    for h in rpr.findall(q("a", "highlight")):
        rpr.remove(h)
    el = etree.Element(q("a", "highlight"))
    etree.SubElement(el, q("a", "srgbClr"), val=HL_RGB)
    _insert_ordered(rpr, el, _A_RPR_AFTER)


def _split_run(run, marks: dict[int, set[int]], rpr_tag: str, add_hl, text_tags: tuple):
    parent = run.getparent()
    if parent is None:
        return
    rpr = run.find(rpr_tag)
    children = [c for c in run if c.tag != rpr_tag]

    def new_run(hl: bool):
        r = etree.Element(run.tag, attrib=dict(run.attrib), nsmap=None)
        rp = copy.deepcopy(rpr) if rpr is not None else (etree.Element(rpr_tag) if hl else None)
        if hl:
            add_hl(rp)
        if rp is not None:
            r.append(rp)
        return r

    pieces: list[tuple[bool, object]] = []
    for c in children:
        offs = marks.get(id(c))
        if c.tag in text_tags and offs:
            text = c.text or ""
            k = 0
            while k < len(text):
                m = k in offs
                j = k
                while j < len(text) and (j in offs) == m:
                    j += 1
                t = etree.Element(c.tag, attrib=dict(c.attrib))
                t.text = text[k:j]
                if c.tag in (W_T, W_DT):
                    t.set(XML_SPACE, "preserve")
                pieces.append((m, t))
                k = j
        else:
            pieces.append((False, c))
    runs: list[tuple[bool, object]] = []
    for flag, el in pieces:
        if not runs or runs[-1][0] != flag:
            runs.append((flag, new_run(flag)))
        runs[-1][1].append(el)
    idx = parent.index(run)
    parent.remove(run)
    for i, (_, r) in enumerate(runs):
        parent.insert(idx + i, r)


class OoxmlDocument(Document):
    def __init__(self, filename: str, data: bytes, kind: str):
        self.filename, self.data, self.kind = filename, data, kind
        self.ext = posixpath.splitext(filename)[1].lower() or "." + kind
        pkg = Package(data, kind)
        self._segments = [u.seg for u in pkg.units]
        self.warnings = pkg.warnings

    @property
    def segments(self) -> list[Segment]:
        return self._segments

    def render_redacted(self, edits, sanitize=True):
        pkg = Package(self.data, self.kind)
        out, missed = pkg.redact(edits, sanitize)
        return out, {"missed": missed}

    def render_highlight(self, spans):
        pkg = Package(self.data, self.kind)
        out, skipped = pkg.highlight(spans)
        return out, {"not_highlighted": skipped}


def extract_segments(data: bytes, kind: str) -> list[Segment]:
    return [u.seg for u in Package(data, kind).units]
