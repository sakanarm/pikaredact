"""Title + name spans: the name only, never the words after it. All names are fictional."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.engine import detect_text  # noqa: E402

CASES = [
    ("นายสมชายเดินทางไปประชุมที่เชียงใหม่", ["สมชาย"]),
    ("นายสมชาย ใจดีมากและขยันทำงาน", ["สมชาย ใจดี"]),
    ("เรียนคุณมานพ เรื่องการต่อสัญญา", ["มานพ"]),
    ("ดร.อนันต์ วงศ์ใหญ่ได้รับรางวัล", ["อนันต์ วงศ์ใหญ่"]),
    ("น.ส.พิมพ์ชนก ทองดี วันที่ 12 มกราคม 2567", ["พิมพ์ชนก ทองดี"]),
    ("นายปิยวัฒน์ ศรีสวัสดิ์วงศา ผู้ตรวจสอบ", ["ปิยวัฒน์ ศรีสวัสดิ์วงศา"]),
    ("ติดต่อคุณสุภาพร คงสวัสดิ์ได้ที่ฝ่ายบัญชี", ["สุภาพร คงสวัสดิ์"]),
    ("นางสาวสุดา รักเรียน ผู้จัดการฝ่ายขาย", ["สุดา รักเรียน"]),
    ("คุณสมศักดิ์จะส่งเอกสารให้ภายในวันศุกร์", ["สมศักดิ์"]),
    ("นายสมชาย จะมาพรุ่งนี้", ["สมชาย"]),
    ("นายบุญมี มีสุข และนางสาวจันทร์เพ็ญ ศรีวงศ์", ["บุญมี มีสุข", "จันทร์เพ็ญ ศรีวงศ์"]),
    ("นายณัฐวุฒิ เกียรติก้องไพศาล", ["ณัฐวุฒิ เกียรติก้องไพศาล"]),
]


@pytest.mark.parametrize("text,names", CASES)
def test_title_name_span(text, names):
    _, r = detect_text(text)
    got = sorted(f.text for f in r.findings if f.entity_type == "PERSON" and f.accepted)
    assert got == sorted(names)


def test_soft_title_pronoun_not_ticked():
    _, r = detect_text("คุณกดปุ่มยืนยันเพื่อดำเนินการต่อ")
    assert not any(f.accepted for f in r.findings if f.entity_type == "PERSON")
