"""Plain text, CSV/TSV, and legacy Office files (converted with LibreOffice when present)."""
from __future__ import annotations

import csv
import html
import io
import os
import shutil
import subprocess
import tempfile

from ..models import Segment
from .base import Document

TEXT_EXT = {".txt", ".md", ".log", ".json", ".xml", ".html", ".htm", ".yaml", ".yml", ".ini", ".conf", ".sql", ".eml"}
CSV_EXT = {".csv", ".tsv"}
LEGACY = {".doc": "docx", ".xls": "xlsx", ".ppt": "pptx", ".rtf": "docx", ".odt": "docx", ".ods": "xlsx", ".odp": "pptx"}


def decode(data: bytes) -> tuple[str, str]:
    if data.startswith(b"\xff\xfe") or data.startswith(b"\xfe\xff"):
        return data.decode("utf-16"), "utf-16"
    try:
        return data.decode("utf-8-sig"), ("utf-8-sig" if data.startswith(b"\xef\xbb\xbf") else "utf-8")
    except UnicodeDecodeError:
        pass
    try:
        return data.decode("cp874"), "cp874"  # Windows Thai (TIS-620 superset)
    except UnicodeDecodeError:
        return data.decode("utf-8", errors="replace"), "utf-8"


def _split_lines(text: str) -> list[tuple[str, str]]:
    out = []
    for raw in text.splitlines(keepends=True):
        body = raw.rstrip("\r\n")
        out.append((body, raw[len(body):]))
    if not out:
        out.append(("", ""))
    return out


def _html_page(title: str, body: str) -> bytes:
    return ("<!doctype html><html lang=th><meta charset=utf-8><title>" + html.escape(title) + "</title>"
            "<style>body{font-family:'Noto Sans Thai','Leelawadee UI',Tahoma,sans-serif;margin:24px;color:#1f2937;line-height:1.7}"
            "mark{background:#fde047;padding:0 1px;border-radius:2px}pre{white-space:pre-wrap;font:inherit}"
            "table{border-collapse:collapse}td{border:1px solid #d1d5db;padding:4px 8px;vertical-align:top}"
            ".note{color:#6b7280;font-size:13px;margin-bottom:16px}</style>"
            "<div class=note>PikaRedact · ไฮไลต์ข้อมูลส่วนบุคคลที่ตรวจพบ (ยังไม่ได้ปิดข้อมูล)</div>" + body + "</html>").encode("utf-8")


def _mark(text: str, spans: list[tuple[int, int]]) -> str:
    out, pos = [], 0
    for s, e in sorted(spans):
        if s < pos:
            s = pos
        if e <= s:
            continue
        out.append(html.escape(text[pos:s]))
        out.append("<mark>" + html.escape(text[s:e]) + "</mark>")
        pos = e
    out.append(html.escape(text[pos:]))
    return "".join(out)


class TextDocument(Document):
    kind = "text"

    def __init__(self, filename: str, data: bytes | None = None, text: str | None = None):
        self.filename = filename
        self.ext = os.path.splitext(filename)[1].lower() or ".txt"
        self.warnings: list[str] = []
        if text is None:
            text, self.encoding = decode(data or b"")
        else:
            self.encoding = "utf-8"
        if self.encoding == "cp874":
            self.warnings.append("ไฟล์ใช้รหัสอักษร TIS-620/Windows-874 — ไฟล์ผลลัพธ์จะบันทึกเป็น UTF-8")
        self.lines = _split_lines(text)
        self._segments = [Segment(f"l{i + 1}", body, f"บรรทัด {i + 1}", "ข้อความ", "body") for i, (body, _) in enumerate(self.lines) if body.strip()]

    @property
    def segments(self):
        return self._segments

    def _bytes(self, s: str) -> bytes:
        bom = self.encoding in ("utf-8-sig", "cp874", "utf-16")
        return (("\ufeff" + s) if bom else s).encode("utf-8")

    def render_redacted(self, edits, sanitize=True):
        from ..redact.operators import apply_edits

        out = []
        for i, (body, end) in enumerate(self.lines):
            e = edits.get(f"l{i + 1}")
            out.append((apply_edits(body, e) if e else body) + end)
        return self._bytes("".join(out)), {"missed": 0}

    def render_highlight(self, spans):
        rows = []
        for i, (body, end) in enumerate(self.lines):
            sp = spans.get(f"l{i + 1}")
            rows.append((_mark(body, sp) if sp else html.escape(body)) + ("\n" if end else ""))
        return _html_page(self.filename, "<pre>" + "".join(rows) + "</pre>"), {"not_highlighted": 0}

    @property
    def highlight_ext(self):
        return ".html"


class CsvDocument(Document):
    kind = "csv"

    def __init__(self, filename: str, data: bytes):
        self.filename = filename
        self.ext = os.path.splitext(filename)[1].lower()
        self.warnings: list[str] = []
        text, self.encoding = decode(data)
        sample = text[:20000]
        try:
            self.dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        except csv.Error:
            self.dialect = csv.excel_tab if self.ext == ".tsv" else csv.excel
        self.newline = "\r\n" if "\r\n" in sample else "\n"
        self.rows = list(csv.reader(io.StringIO(text), self.dialect))
        self._segments = []
        for r, row in enumerate(self.rows):
            for c, val in enumerate(row):
                if val.strip():
                    self._segments.append(Segment(f"r{r + 1}c{c + 1}", val, f"แถว {r + 1} คอลัมน์ {c + 1}", "ตาราง", "table", ("xlsx:csv", r, c)))

    @property
    def segments(self):
        return self._segments

    def render_redacted(self, edits, sanitize=True):
        from ..redact.operators import apply_edits

        buf = io.StringIO()
        w = csv.writer(buf, delimiter=self.dialect.delimiter, quotechar=self.dialect.quotechar or '"', lineterminator=self.newline)
        for r, row in enumerate(self.rows):
            w.writerow([apply_edits(v, edits[f"r{r + 1}c{c + 1}"]) if f"r{r + 1}c{c + 1}" in edits else v for c, v in enumerate(row)])
        return ("\ufeff" + buf.getvalue()).encode("utf-8"), {"missed": 0}  # BOM so Excel shows Thai correctly

    def render_highlight(self, spans):
        trs = []
        for r, row in enumerate(self.rows):
            tds = []
            for c, v in enumerate(row):
                sp = spans.get(f"r{r + 1}c{c + 1}")
                tds.append("<td>" + (_mark(v, sp) if sp else html.escape(v)) + "</td>")
            trs.append("<tr>" + "".join(tds) + "</tr>")
        return _html_page(self.filename, "<table>" + "".join(trs) + "</table>"), {"not_highlighted": 0}

    @property
    def highlight_ext(self):
        return ".html"


def soffice_path() -> str | None:
    for name in ("soffice", "libreoffice"):
        p = shutil.which(name)
        if p:
            return p
    for p in (r"C:\Program Files\LibreOffice\program\soffice.exe", r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
              "/usr/bin/soffice", "/usr/lib/libreoffice/program/soffice", "/opt/libreoffice/program/soffice"):
        if os.path.exists(p):
            return p
    return None


def soffice_convert(ext: str, data: bytes, target: str, timeout: int = 180) -> bytes | None:
    """Convert bytes of type `ext` to `target` with headless LibreOffice. None when it is missing or fails.
    Each call gets its own profile folder, so conversions can run side by side."""
    exe = soffice_path()
    if not exe:
        return None
    with tempfile.TemporaryDirectory(prefix="pikaredact_") as tmp:
        src = os.path.join(tmp, "input" + ext)
        with open(src, "wb") as fh:
            fh.write(data)
        profile = "file:///" + os.path.join(tmp, "profile").replace("\\", "/").lstrip("/")
        try:
            subprocess.run([exe, f"-env:UserInstallation={profile}", "--headless", "--norestore", "--convert-to", target,
                            "--outdir", tmp, src], check=False, timeout=timeout, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            return None
        out = os.path.join(tmp, "input." + target.split(":")[0])
        if not os.path.exists(out):
            return None
        with open(out, "rb") as fh:
            return fh.read()


def convert_legacy(filename: str, data: bytes) -> tuple[str, bytes]:
    ext = os.path.splitext(filename)[1].lower()
    target = LEGACY[ext]
    if not soffice_path():
        raise ValueError(f"ไฟล์ {ext} รุ่นเก่ายังเปิดบนเครื่องนี้ไม่ได้ — กรุณาบันทึกเป็น .{target} ก่อนอัปโหลด")
    converted = soffice_convert(ext, data, target)
    if converted is None:
        raise ValueError(f"แปลงไฟล์ {ext} ไม่สำเร็จ — กรุณาบันทึกเป็น .{target} ก่อนอัปโหลด")
    base = os.path.splitext(filename)[0]
    return base + "." + target, converted
