"""Rule packs (law / standard) and the sensitive-data recognizers. All sample text is fictional."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.detect.tables import label_type  # noqa: E402
from app.engine import detect_text  # noqa: E402
from app.models import ENTITY_TYPES, SENSITIVE_TYPES  # noqa: E402
from app.packs import DEFAULT_PACKS, PACKS, clean_pack_ids, effective_types, law_map, pack_types  # noqa: E402
from app.redact.fake_th import FakeTH  # noqa: E402
from app.redact.operators import mask_value  # noqa: E402


# ---------------------------------------------------------------- catalogue
def test_every_type_is_in_a_pack():
    covered = {t for p in PACKS for t in p["types"]}
    assert set(ENTITY_TYPES) - covered == set()


def test_pack_fields():
    ids = [p["id"] for p in PACKS]
    assert len(ids) == len(set(ids))
    for p in PACKS:
        assert p["kind"] in ("law", "standard", "policy") and p["region"] in ("th", "intl")
        assert p["name"] and p["short"] and p["law"] and p["desc"]
        assert all(t in ENTITY_TYPES for t in p["types"]), p["id"]


def test_sensitive_pack_is_section_26():
    p = next(p for p in PACKS if p["id"] == "pdpa_sensitive")
    assert set(p["types"]) == set(SENSITIVE_TYPES)
    assert len(SENSITIVE_TYPES) == 10  # PDPA s.26's eleven categories, with race and ethnicity as one type


def test_locked_and_secret_always_on():
    assert "th_cyber" in clean_pack_ids([])
    assert clean_pack_ids(["nope", "gdpr"]) == ["th_cyber", "gdpr"]
    assert pack_types([]) == ["SECRET", "IP_ADDRESS", "DEVICE_ID"]
    assert "SECRET" in effective_types([], ["SECRET"])


def test_effective_types():
    eff = effective_types(DEFAULT_PACKS, ["DATE_OF_BIRTH"])
    assert "DATE_OF_BIRTH" not in eff and "URL" not in eff  # URL only comes with HIPAA
    assert "HEALTH" in eff and "ORGANIZATION" in eff
    assert "URL" in effective_types(DEFAULT_PACKS + ["hipaa"], [])
    no_sensitive = effective_types([p for p in DEFAULT_PACKS if p not in ("pdpa_sensitive", "th_health")], [])
    assert "RELIGION" not in no_sensitive and "PERSON" in no_sensitive


def test_law_map_cites_every_type():
    laws = law_map()
    assert all(laws[t] for t in ENTITY_TYPES)
    assert "PDPA ม.26" in laws["HEALTH"]


# ---------------------------------------------------------------- detection
POSITIVE = [
    ("โรคประจำตัว: เบาหวาน", "HEALTH", "เบาหวาน"),
    ("นายสมมติ ทดลองดี ป่วยเป็นมะเร็งเต้านม", "HEALTH", "มะเร็งเต้านม"),
    ("ศาสนา: อิสลาม", "RELIGION", "อิสลาม"),
    ("เชื้อชาติ: จีน", "RACE_ETHNICITY", "จีน"),
    ("รสนิยมทางเพศ: รักร่วมเพศ", "SEXUAL_BEHAVIOR", "รักร่วมเพศ"),
    ("ประวัติอาชญากรรม: เคยต้องโทษคดียาเสพติด", "CRIMINAL_RECORD", "เคยต้องโทษคดียาเสพติด"),
    ("เป็นกรรมการสหภาพแรงงานรัฐวิสาหกิจสมมติ", "TRADE_UNION", None),
    ("ผลตรวจพันธุกรรม: BRCA1 positive", "GENETIC", "BRCA1 positive"),
    ("ประเภทความพิการ: ทางการเคลื่อนไหว", "DISABILITY", "ทางการเคลื่อนไหว"),
    ("HN: 65-012345", "MEDICAL_RECORD", "65-012345"),
    ("เลขที่กรมธรรม์ 1234-567890", "INSURANCE_ID", "1234-567890"),
    ("ใบขับขี่เลขที่ 62001234", "LICENSE_NUMBER", "62001234"),
    ("IMEI: 490154203237518", "DEVICE_ID", "490154203237518"),
    ("MAC: 00:1A:2B:3C:4D:5E", "DEVICE_ID", "00:1A:2B:3C:4D:5E"),
    ("พิกัด 13.7563, 100.5018", "GPS_LOCATION", "13.7563, 100.5018"),
    ("LINE ID: somchai_test", "SOCIAL_MEDIA", "somchai_test"),
    ("CVV: 123", "CARD_SECURITY", "123"),
    ("เงินเดือน: 45,000 บาท", "SALARY", None),
    ("IPv6 2001:db8:85a3::8a2e:370:7334", "IP_ADDRESS", "2001:db8:85a3::8a2e:370:7334"),
    ("เว็บส่วนตัว https://example.com/u/somchai", "URL", "https://example.com/u/somchai"),
]

NEGATIVE = [
    "ปีพุทธศักราช 2567 และคริสต์ศักราช 2024",
    "ระบบเฟส: ทดสอบระบบ",
    "threads: 16",
    "Line: 120",
    "standard EN 50160 compliance",
    "export 12/25 records",
    "รายได้รวมของบริษัท 1,234,567 บาท",
    "SSH key fingerprint: SHA256:abcdEFGH1234",
    "ค่าแรงขั้นต่ำ 350 บาท",
    "ผลการตรวจ: ปกติ",
    "โรคประจำตัว: ไม่มี",
    "p = 0.0123, 0.0456",
    "@dataclass",
    "ตรวจพบช่องโหว่ในระบบ",
    "พิธีทางศาสนาในวันหยุด",
    "ฟ้าขมุกขมัวทั้งวัน",
    "ดาวน์โหลดได้ที่ https://www.python.org/downloads",  # bare URL: suggestion only
]


@pytest.mark.parametrize("text,etype,value", POSITIVE, ids=[p[1] + ":" + p[0][:12] for p in POSITIVE])
def test_sensitive_positive(text, etype, value):
    _, res = detect_text(text)
    hits = [f for f in res.findings if f.entity_type == etype and f.accepted]
    assert hits, f"{etype} not found in {text!r}: {[(f.entity_type, f.text) for f in res.findings]}"
    if value:
        assert any(value in f.text for f in hits), [f.text for f in hits]


@pytest.mark.parametrize("text", NEGATIVE)
def test_sensitive_negative(text):
    _, res = detect_text(text)
    assert not [f for f in res.findings if f.accepted], [(f.entity_type, f.text, f.score) for f in res.findings]


def test_entities_filter_drops_disabled_types():
    _, res = detect_text("ศาสนา: อิสลาม โทร 081-234-5678", entities=["PHONE"])
    assert {f.entity_type for f in res.findings} == {"PHONE"}


@pytest.mark.parametrize("label,etype", [
    ("โรคประจำตัว", "HEALTH"), ("ศาสนา", "RELIGION"), ("HN", "MEDICAL_RECORD"), ("ที่อยู่ IP", "IP_ADDRESS"),
    ("ที่อยู่", "ADDRESS"), ("MAC Address", "DEVICE_ID"), ("Line ID", "SOCIAL_MEDIA"), ("เงินเดือน", "SALARY"),
    ("CVV", "CARD_SECURITY"), ("เลขที่กรมธรรม์", "INSURANCE_ID"), ("Line", None), ("Expiry Date", None),
    ("Fingerprint", None), ("รายได้", None),
])
def test_table_labels(label, etype):
    assert label_type(label) == etype


# ---------------------------------------------------------------- redaction
def test_mask_hides_sensitive_values():
    for t in SENSITIVE_TYPES:
        assert mask_value(t, "เบาหวาน") == "xxxx"
    assert mask_value("CARD_SECURITY", "123") == "xxx"
    assert mask_value("SALARY", "45,000 บาท") == "xxxx"


def test_fake_never_invents_sensitive_facts():
    fake = FakeTH(1)
    assert fake.make("HEALTH", "เบาหวาน") == "[ข้อมูลสุขภาพ]"
    assert fake.make("RELIGION", "อิสลาม").startswith("[")
    assert fake.make("IP_ADDRESS", "2001:db8::1").startswith("2001:db8::")
