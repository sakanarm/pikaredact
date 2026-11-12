"""Layer 2b — structure rules for tables and spreadsheets.

IT documents keep most PII in tables: revision history ("Author"), sign-off
blocks ("Approved by"), contact lists and test-data sheets. If a header or the
label to the left says "ชื่อ-นามสกุล" / "Phone" / "Password", the value cell is
PII even when no pattern or model recognises it.
"""
from __future__ import annotations

import re
from collections import defaultdict

from ..models import Finding, Segment

_LABEL_RULES: list[tuple[str, re.Pattern]] = [(t, re.compile(p, re.IGNORECASE)) for t, p in [
    ("SECRET", r"^(password|passwd|pwd|pass|รหัสผ่าน|secret|api ?key|token|access ?key)$"),
    ("TH_ID_CARD", r"(เลข(ที่)?บัตร(ประจำตัว)?ประชาชน|เลขประจำตัวประชาชน|บัตรประชาชน|citizen ?id|national ?id|id ?card|thai ?id)"),
    ("PHONE", r"(เบอร์|โทรศัพท์|^โทร\.?$|มือถือ|^tel\.?$|telephone|phone|mobile|^fax$|contact (no|number))"),
    ("EMAIL", r"(e-?mail|อีเมล|อีเมล์)"),
    ("BANK_ACCOUNT", r"(เลขที่บัญชี|เลขบัญชี|account (no|number)|bank account)"),
    ("DATE_OF_BIRTH", r"(วันเกิด|วัน ?เดือน ?ปี ?เกิด|date of birth|birth ?date|^dob$)"),
    ("ADDRESS", r"(ที่อยู่|^address$|home address|mailing address)"),
    ("PASSPORT", r"(passport|พาสปอร์ต|หนังสือเดินทาง)"),
    ("OTHER_ID", r"(รหัสพนักงาน|employee ?(id|no|code)|staff ?id|รหัสลูกค้า|customer ?(id|no|code)|^hn$|hospital number|member ?id|^username$|^user ?id$|ชื่อผู้ใช้)"),
    ("ORGANIZATION", r"(ชื่อบริษัท|ชื่อหน่วยงาน|^company( name)?$|^client( name)?$|^organi[sz]ation$)"),
    ("PERSON", r"(^ชื่อ$|ชื่อ ?-? ?(นามสกุล|สกุล)|^นามสกุล$|^สกุล$|ชื่อจริง|ชื่อเล่น|ชื่อ(ผู้ติดต่อ|พนักงาน|ลูกค้า)"
               r"|^ผู้(จัดทำ|ตรวจสอบ|ตรวจ|อนุมัติ|ทดสอบ|รับผิดชอบ|ติดต่อ|ประสานงาน|บันทึก|แจ้ง|ร้องขอ|ลงนาม|เขียน|แก้ไข|ขอ)"
               r"|^(full ?|first ?|last ?|sur|nick|customer |contact |employee |staff )?name$"
               r"|^(prepared|reviewed|approved|verified|checked|tested|requested|created|modified|updated|signed|written) by$"
               r"|^(author|owner|assignee|approver|reviewer|tester|requester|reporter|contact person|pic|person in charge|signature)$"
               r"|ลายมือชื่อ|^ลงชื่อ$)"),
]]


def _clean_label(text: str) -> str:
    t = re.sub(r"[\s:*：()\[\]]+$", "", text.strip())
    t = re.sub(r"^[\s:*：]+", "", t)
    return re.sub(r"\s+", " ", t)


def label_type(text: str) -> str | None:
    t = _clean_label(text)
    if not t or len(t) > 40 or re.search(r"\d{3,}", t):
        return None
    for etype, rx in _LABEL_RULES:
        if rx.search(t):
            return etype
    return None


def _value_ok(text: str) -> bool:
    t = text.strip()
    if not t or len(t) > 200 or not re.search(r"[0-9A-Za-zก-๎]", t):
        return False
    if t.lower() in {"-", "n/a", "na", "none", "null", "tbd", "xxx", "ไม่มี"} or re.fullmatch(r"[xX\-_.*•]+", t):
        return False
    return label_type(t) is None  # the value is another label, not data


def _looks_like_data(text: str) -> bool:
    t = text.strip()
    return bool(re.search(r"\d|@", t)) or (len(t.split()) >= 2 and label_type(t) is None)


def table_findings(segments: list[Segment]) -> list[Finding]:
    # (table, row, col) -> [segment]
    cells: dict[tuple, list[Segment]] = defaultdict(list)
    for seg in segments:
        if not seg.cell:
            continue
        # xlsx shared strings: one segment can back many cells
        for key in (seg.cell if isinstance(seg.cell, list) else [seg.cell]):
            cells[tuple(key)].append(seg)
    if not cells:
        return []

    def cell_text(key):
        return " ".join(s.text for s in cells.get(key, [])).strip()

    by_table: dict = defaultdict(set)
    for (table, row, col) in cells:
        by_table[table].add((row, col))

    out: list[Finding] = []
    seen: set[str] = set()

    def flag(key, etype, score, how):
        for seg in cells.get(key, []):
            if seg.id in seen or not _value_ok(seg.text):
                continue
            s = len(seg.text) - len(seg.text.lstrip())
            e = len(seg.text.rstrip())
            if e > s:
                seen.add(seg.id)
                out.append(Finding(seg.id, s, e, seg.text[s:e], etype, score, [f"table:{how}"]))

    for table, coords in by_table.items():
        ncols = len({c for _, c in coords})
        rows = sorted({r for r, _ in coords})
        # label to the left: "Prepared by | สมชาย ใจดี"
        for (r, c) in coords:
            if c == 0:
                continue
            if ncols >= 3 and r == rows[0] and not _looks_like_data(cell_text((table, r, c))):
                continue  # header row "ชื่อ-นามสกุล | ตำแหน่ง | ..." — the right cell is another heading
            lt = label_type(cell_text((table, r, c - 1)))
            if lt:
                flag((table, r, c), lt, 0.6, "label_left")
        # header above: column whose header says "ชื่อ-นามสกุล" (skip 2-column forms)
        if ncols < 3 and not str(table).startswith("xlsx:"):
            continue
        top = rows[:10]
        for col in sorted({c for _, c in coords}):
            for r in top:
                if (r, col) not in coords:
                    continue
                lt = label_type(cell_text((table, r, col)))
                if lt:
                    for r2 in rows:
                        if r2 > r and (r2, col) in coords:
                            flag((table, r2, col), lt, 0.55, "header")
                    break
    return out
