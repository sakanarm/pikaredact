"""Open any supported file as a Document, and verify redacted outputs."""
from __future__ import annotations

import os
import re

from ..normalize import normalize
from .base import Document
from .text import CSV_EXT, LEGACY, TEXT_EXT, CsvDocument, TextDocument, convert_legacy

OOXML_EXT = {".docx": "docx", ".docm": "docx", ".dotx": "docx", ".xlsx": "xlsx", ".xlsm": "xlsx", ".pptx": "pptx", ".pptm": "pptx", ".ppsx": "pptx"}
SUPPORTED = sorted(set(OOXML_EXT) | {".pdf"} | TEXT_EXT | CSV_EXT | set(LEGACY))
_DIGIT_TYPES = {"PHONE", "TH_ID_CARD", "BANK_ACCOUNT", "CREDIT_CARD", "PASSPORT", "OTHER_ID"}


def open_document(filename: str, data: bytes) -> Document:
    ext = os.path.splitext(filename)[1].lower()
    if ext in LEGACY:
        new_name, data = convert_legacy(filename, data)
        doc = open_document(new_name, data)
        doc.warnings.insert(0, f"แปลงไฟล์ {ext} เป็น {os.path.splitext(new_name)[1]} อัตโนมัติ — ไฟล์ผลลัพธ์จะเป็นรูปแบบใหม่")
        return doc
    if ext in OOXML_EXT or (data[:2] == b"PK" and ext not in TEXT_EXT | CSV_EXT):
        from .ooxml import OoxmlDocument, kind_of

        kind = OOXML_EXT.get(ext) or _sniff_ooxml(data)
        if not kind:
            raise ValueError("ไม่รู้จักรูปแบบไฟล์นี้")
        if data[:2] != b"PK":
            raise ValueError("ไฟล์เสียหรือไม่ใช่ไฟล์ Office ที่ถูกต้อง (ถ้ามีรหัสผ่าน กรุณาปลดรหัสก่อน)")
        return OoxmlDocument(filename, data, kind)
    if ext == ".pdf" or data[:5] == b"%PDF-":
        from .pdf import PdfDocument

        return PdfDocument(filename, data)
    if ext in CSV_EXT:
        return CsvDocument(filename, data)
    if ext in TEXT_EXT or not ext:
        return TextDocument(filename, data)
    raise ValueError(f"ยังไม่รองรับไฟล์ {ext or 'ชนิดนี้'} — รองรับ Word, Excel, PowerPoint, PDF และไฟล์ข้อความ")


def _sniff_ooxml(data: bytes) -> str | None:
    import io
    import zipfile

    try:
        names = set(zipfile.ZipFile(io.BytesIO(data)).namelist())
    except zipfile.BadZipFile:
        return None
    if "word/document.xml" in names:
        return "docx"
    if "xl/workbook.xml" in names:
        return "xlsx"
    if "ppt/presentation.xml" in names:
        return "pptx"
    return None


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", normalize(s)).casefold()


def find_leaks(output: bytes, filename: str, originals: list[tuple[str, str]]) -> list[dict]:
    """Re-read the produced file and look for any original value that is still there.

    originals: [(entity_type, original_text)]. Values under 4 characters are skipped
    (too many false alarms); digit values are compared on digits only.
    """
    try:
        doc = open_document(filename, output)
    except Exception as exc:  # the output must at least open again
        return [{"type": "", "value": "", "where": f"เปิดไฟล์ผลลัพธ์ไม่ได้: {exc}"}]
    texts = [(s.label, _norm(s.text), re.sub(r"\D", "", normalize(s.text))) for s in doc.segments]
    stem = os.path.splitext(os.path.basename(filename))[0]
    texts.append(("ชื่อไฟล์", _norm(stem), re.sub(r"\D", "", normalize(stem))))
    hidden = getattr(doc, "hidden_texts", None)  # PDF: /ActualText, /Alt… that viewers don't show
    if hidden:
        texts += [(lab, _norm(t), re.sub(r"\D", "", normalize(t))) for lab, t in hidden()]
    leaks, seen = [], set()
    for etype, value in originals:
        v = _norm(value).strip()
        digits = re.sub(r"\D", "", normalize(value))
        if (etype, v) in seen:
            continue
        seen.add((etype, v))
        use_digits = etype in _DIGIT_TYPES and len(digits) >= 8 and len(digits) >= len(v.replace(" ", "")) * 0.6
        if not use_digits and len(v) < 4:
            continue
        for label, t, d in texts:
            if (use_digits and digits in d) or (not use_digits and v in t):
                leaks.append({"type": etype, "value": value, "where": label})
                break
    return leaks


__all__ = ["Document", "open_document", "find_leaks", "SUPPORTED"]
