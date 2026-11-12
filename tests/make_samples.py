"""Build small synthetic Thai IT documents in every supported format (fictional data only).

Each file hides PII in the places real documents do: tables, split runs, headers,
comments, notes, tracked deletions, metadata, hidden sheets and number cells.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.detect.validators import thai_id_check_digit  # noqa: E402

ID12 = "310050012345"
TH_ID = ID12 + thai_id_check_digit(ID12)
TH_ID_FMT = f"{TH_ID[0]}-{TH_ID[1:5]}-{TH_ID[5:10]}-{TH_ID[10:12]}-{TH_ID[12]}"

# value, type — what a correct redaction must remove
EXPECTED = [
    ("สมชาย ใจดี", "PERSON"),
    ("วรรณา ศรีสุข", "PERSON"),
    ("081-234-5678", "PHONE"),
    ("somchai.j@example.co.th", "EMAIL"),
    (TH_ID_FMT, "TH_ID_CARD"),
    ("123-4-56789-0", "BANK_ACCOUNT"),
    ("10.20.30.41", "IP_ADDRESS"),
    ("P@ssw0rd!2024", "SECRET"),
]


def build_docx(path: Path):
    import docx
    from docx.oxml import parse_xml
    from docx.oxml.ns import nsdecls

    d = docx.Document()
    d.core_properties.author = "สมชาย ใจดี"
    d.core_properties.title = "เอกสารออกแบบระบบ CRM"
    d.add_heading("เอกสารออกแบบระบบ (System Design)", 1)
    p = d.add_paragraph("ผู้จัดทำ: ")
    p.add_run("สม").bold = True           # name split across runs
    p.add_run("ชาย ใจดี โทร 081-234-5678")
    d.add_paragraph(f"เลขบัตรประชาชน {TH_ID_FMT} อีเมล somchai.j@example.co.th")
    d.add_paragraph("เครื่อง DB อยู่ที่ 10.20.30.41 รหัสผ่าน: P@ssw0rd!2024 บน Microsoft Azure")
    t = d.add_table(rows=3, cols=3)
    for c, h in enumerate(["ชื่อ-นามสกุล", "ตำแหน่ง", "เบอร์โทร"]):
        t.cell(0, c).text = h
    t.cell(1, 0).text, t.cell(1, 1).text, t.cell(1, 2).text = "วรรณา ศรีสุข", "Project Manager", "081-234-5678"
    t.cell(2, 0).text, t.cell(2, 1).text, t.cell(2, 2).text = "สมชาย ใจดี", "Developer", "-"
    d.sections[0].header.paragraphs[0].text = "จัดทำโดย สมชาย ใจดี"
    d.sections[0].footer.paragraphs[0].text = "บัญชีรับเงิน 123-4-56789-0"
    # tracked deletion that still holds a phone number
    body = d.add_paragraph("ติดต่อสำรอง ")
    body._p.append(parse_xml(
        f'<w:del {nsdecls("w")} w:id="9" w:author="วรรณา ศรีสุข" w:date="2026-01-01T00:00:00Z">'
        '<w:r><w:delText xml:space="preserve">โทร 081-234-5678</w:delText></w:r></w:del>'))
    d.save(path)


def build_xlsx(path: Path):
    import openpyxl
    from openpyxl.comments import Comment

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Users"
    ws.append(["Name", "Email", "Phone", "Citizen ID", "Role"])
    ws.append(["สมชาย ใจดี", "somchai.j@example.co.th", "081-234-5678", int(TH_ID), "admin"])
    ws.append(["วรรณา ศรีสุข", "-", "-", "-", "viewer"])
    ws.append(["สมชาย ใจดี", "", "", "", "duplicate row (shared string)"])
    ws["F1"] = "Server"
    ws["F2"] = "10.20.30.41"
    ws["A2"].comment = Comment("ตรวจแล้วโดย วรรณา ศรีสุข", "วรรณา ศรีสุข")
    hidden = wb.create_sheet("secret")
    hidden["A1"], hidden["B1"] = "Password", "P@ssw0rd!2024"
    hidden["A2"], hidden["B2"] = "Bank account", "123-4-56789-0"
    hidden.sheet_state = "hidden"
    wb.properties.creator = "สมชาย ใจดี"
    wb.save(path)


def build_pptx(path: Path):
    from pptx import Presentation
    from pptx.util import Inches

    prs = Presentation()
    s = prs.slides.add_slide(prs.slide_layouts[1])
    s.shapes.title.text = "Project Kickoff"
    s.placeholders[1].text = "PM: วรรณา ศรีสุข\nDev lead: สมชาย ใจดี (081-234-5678)"
    s2 = prs.slides.add_slide(prs.slide_layouts[5])
    s2.shapes.title.text = "ทีมงาน"
    tbl = s2.shapes.add_table(3, 3, Inches(0.5), Inches(1.5), Inches(9), Inches(1.5)).table
    for c, h in enumerate(["ชื่อ", "อีเมล", "หน้าที่"]):
        tbl.cell(0, c).text = h
    tbl.cell(1, 0).text, tbl.cell(1, 1).text, tbl.cell(1, 2).text = "สมชาย ใจดี", "somchai.j@example.co.th", "Dev"
    tbl.cell(2, 0).text, tbl.cell(2, 1).text, tbl.cell(2, 2).text = "วรรณา ศรีสุข", "-", "PM"
    s2.notes_slide.notes_text_frame.text = f"อย่าลืมโอนเข้าบัญชี 123-4-56789-0 / เลขบัตร {TH_ID_FMT} / server 10.20.30.41 / pwd P@ssw0rd!2024"
    prs.core_properties.author = "สมชาย ใจดี"
    prs.save(path)


def build_pdf(path: Path):
    import pymupdf as fitz

    from app import config

    doc = fitz.open()
    page = doc.new_page()
    css = "* {font-family: th; font-size: 12px; line-height: 1.6;}"
    arch = None
    if config.PDF_THAI_FONT:
        arch = fitz.Archive(os.path.dirname(config.PDF_THAI_FONT))
        css = f"@font-face {{font-family: th; src: url('{os.path.basename(config.PDF_THAI_FONT)}');}} " + css
    html = (
        "<h3>บันทึกการประชุม</h3>"
        "<p>ผู้เข้าร่วม: สมชาย ใจดี, วรรณา ศรีสุข</p>"
        "<p>ติดต่อ 081-234-5678 หรือ somchai.j@example.co.th</p>"
        f"<p>เลขบัตรประชาชน {TH_ID_FMT} บัญชี 123-4-56789-0</p>"
        "<p>IP เครื่อง 10.20.30.41 รหัสผ่าน: P@ssw0rd!2024</p>"
    )
    page.insert_htmlbox(fitz.Rect(50, 50, 550, 800), html, css=css, archive=arch)
    page.add_text_annot((60, 400), "ฝากโทรหา สมชาย ใจดี 081-234-5678")
    doc.set_metadata({"author": "สมชาย ใจดี", "title": "บันทึกการประชุม"})
    doc.save(path)


def build_txt(path: Path):
    path.write_text(
        "บันทึกส่งมอบงาน\nผู้ส่ง: สมชาย ใจดี โทร 081-234-5678\nอีเมล somchai.j@example.co.th\n"
        f"บัตรประชาชน {TH_ID_FMT}\nรหัสผ่าน: P@ssw0rd!2024\n", encoding="utf-8")


def build_csv(path: Path):
    path.write_text(
        "ชื่อ-นามสกุล,อีเมล,เบอร์โทร,เลขบัญชี\n"
        "สมชาย ใจดี,somchai.j@example.co.th,081-234-5678,123-4-56789-0\n"
        "วรรณา ศรีสุข,-,-,-\n", encoding="utf-8-sig")


BUILDERS = {"docx": build_docx, "xlsx": build_xlsx, "pptx": build_pptx, "pdf": build_pdf, "txt": build_txt, "csv": build_csv}


def build_all(outdir: Path) -> list[Path]:
    outdir.mkdir(parents=True, exist_ok=True)
    out = []
    for ext, fn in BUILDERS.items():
        p = outdir / f"sample_th.{ext}"
        fn(p)
        out.append(p)
    return out


if __name__ == "__main__":
    for p in build_all(ROOT / "tests" / "out"):
        print(p)
