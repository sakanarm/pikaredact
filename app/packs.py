"""Predefined rule packs: which data types to look for, grouped by the law or standard that protects them.

A pack only switches entity types on; the recognizers themselves live in app/detect.
Effective types for a job = union of enabled packs (+ locked packs, + SECRET) minus per-type switches.
Citations are for orientation only; they are not legal advice.
"""
from __future__ import annotations

from .models import ENTITY_TYPES, SENSITIVE_TYPES

_IDENTIFIERS = [
    "PERSON", "TH_ID_CARD", "PASSPORT", "LICENSE_NUMBER", "PHONE", "EMAIL", "ADDRESS", "DATE_OF_BIRTH",
    "BANK_ACCOUNT", "CREDIT_CARD", "TH_LICENSE_PLATE", "IP_ADDRESS", "DEVICE_ID", "GPS_LOCATION",
    "SOCIAL_MEDIA", "OTHER_ID", "SALARY", "INSURANCE_ID", "MEDICAL_RECORD",
]
_SENSITIVE = [t for t in ENTITY_TYPES if t in SENSITIVE_TYPES]

# kind: law = statute or regulation, standard = industry/ISO standard, policy = organisation policy (not a law)
# region: th = Thai, intl = foreign or international
PACKS: list[dict] = [
    {
        "id": "pdpa_general", "region": "th", "kind": "law", "default": True, "locked": False,
        "name": "PDPA ข้อมูลส่วนบุคคลทั่วไป",
        "short": "PDPA ม.6",
        "law": "พ.ร.บ.คุ้มครองข้อมูลส่วนบุคคล พ.ศ. 2562",
        "sections": "มาตรา 6 (นิยาม \"ข้อมูลส่วนบุคคล\") · มาตรา 37 (หน้าที่รักษาความมั่นคงปลอดภัย) · "
                    "ประกาศคณะกรรมการคุ้มครองข้อมูลส่วนบุคคล เรื่อง มาตรการรักษาความมั่นคงปลอดภัยฯ พ.ศ. 2565",
        "desc": "ข้อมูลที่ทำให้ระบุตัวบุคคลได้ ทั้งทางตรงและทางอ้อม เช่น ชื่อ เลขบัตร เบอร์โทร ที่อยู่ "
                "ข้อมูลการเงิน และตัวระบุออนไลน์ แนวทางการทำข้อมูลนิรนามดูได้จากเอกสาร Data Anonymization ของ สพร. (DGA)",
        "types": _IDENTIFIERS,
    },
    {
        "id": "pdpa_sensitive", "region": "th", "kind": "law", "default": True, "locked": False,
        "name": "PDPA ข้อมูลอ่อนไหว",
        "short": "PDPA ม.26",
        "law": "พ.ร.บ.คุ้มครองข้อมูลส่วนบุคคล พ.ศ. 2562",
        "sections": "มาตรา 26",
        "desc": "เชื้อชาติ เผ่าพันธุ์ ความคิดเห็นทางการเมือง ความเชื่อทางศาสนาหรือปรัชญา พฤติกรรมทางเพศ "
                "ประวัติอาชญากรรม ข้อมูลสุขภาพ ความพิการ ข้อมูลสหภาพแรงงาน ข้อมูลพันธุกรรม และข้อมูลชีวภาพ "
                "ต้องได้รับความยินยอมโดยชัดแจ้ง และมีโทษสูงกว่าข้อมูลทั่วไป",
        "types": _SENSITIVE,
    },
    {
        "id": "th_health", "region": "th", "kind": "law", "default": True, "locked": False,
        "name": "ข้อมูลสุขภาพและเวชระเบียน",
        "short": "พ.ร.บ.สุขภาพฯ ม.7",
        "law": "พ.ร.บ.สุขภาพแห่งชาติ พ.ศ. 2550 · ประมวลกฎหมายอาญา",
        "sections": "พ.ร.บ.สุขภาพแห่งชาติ มาตรา 7 (ข้อมูลสุขภาพเป็นความลับส่วนบุคคล) · ประมวลกฎหมายอาญา มาตรา 323 (เปิดเผยความลับที่รู้จากวิชาชีพ)",
        "desc": "โรคประจำตัว ผลการวินิจฉัย ประวัติการแพ้ยา เลขเวชระเบียน HN/AN สิทธิการรักษาและเลขกรมธรรม์ ความพิการ และผลตรวจพันธุกรรม",
        "types": ["HEALTH", "MEDICAL_RECORD", "INSURANCE_ID", "DISABILITY", "GENETIC"],
    },
    {
        "id": "th_cyber", "region": "th", "kind": "law", "default": True, "locked": True,
        "name": "ความลับทางเทคนิคและการเข้าถึงระบบ",
        "short": "พ.ร.บ.คอมพิวเตอร์ฯ ม.6",
        "law": "พ.ร.บ.ว่าด้วยการกระทำความผิดเกี่ยวกับคอมพิวเตอร์ พ.ศ. 2550 (แก้ไข พ.ศ. 2560) · พ.ร.บ.การรักษาความมั่นคงปลอดภัยไซเบอร์ พ.ศ. 2562 · ISO/IEC 27001:2022",
        "sections": "พ.ร.บ.คอมพิวเตอร์ฯ มาตรา 6 (เปิดเผยมาตรการป้องกันการเข้าถึง) · ISO/IEC 27001:2022 Annex A 5.17 (Authentication information), 8.11 (Data masking), 8.12 (Data leakage prevention)",
        "desc": "รหัสผ่าน API key token กุญแจส่วนตัว IP ภายใน และเลขอุปกรณ์ ชุดนี้เปิดเสมอ เพราะรหัสผ่านที่หลุดออกไปเป็นความเสี่ยงทันที",
        "types": ["SECRET", "IP_ADDRESS", "DEVICE_ID"],
    },
    {
        "id": "pci_dss", "region": "intl", "kind": "standard", "default": True, "locked": False,
        "name": "ข้อมูลบัตรชำระเงิน",
        "short": "PCI DSS Req.3",
        "law": "PCI DSS v4.0.1 (มาตรฐานอุตสาหกรรมบัตรชำระเงิน)",
        "sections": "Requirement 3 (Protect stored account data): 3.3 ห้ามเก็บ CVV หลังอนุมัติ · 3.4 แสดงเลขบัตรแบบปิดบัง",
        "desc": "เลขบัตรเครดิต/เดบิต (PAN) รหัส CVV/CVC และวันหมดอายุบัตร โหมดปิดบางส่วนจะเหลือเลขท้าย 4 หลักตามที่มาตรฐานอนุญาต",
        "types": ["CREDIT_CARD", "CARD_SECURITY"],
    },
    {
        "id": "th_finance", "region": "th", "kind": "law", "default": True, "locked": False,
        "name": "ความลับทางการเงินของลูกค้า",
        "short": "สถาบันการเงินฯ / ข้อมูลเครดิตฯ",
        "law": "พ.ร.บ.ธุรกิจสถาบันการเงิน พ.ศ. 2551 · พ.ร.บ.การประกอบธุรกิจข้อมูลเครดิต พ.ศ. 2545",
        "sections": "หมวดว่าด้วยการรักษาความลับข้อมูลลูกค้าและข้อมูลเครดิต (ไม่ระบุเลขมาตรา โปรดตรวจกับฝ่ายกฎหมาย)",
        "desc": "เลขบัญชี เลขบัตร เงินเดือน รายได้ และข้อมูลที่ใช้ประเมินเครดิตของลูกค้า",
        "types": ["BANK_ACCOUNT", "CREDIT_CARD", "CARD_SECURITY", "SALARY"],
    },
    {
        "id": "th_gov_info", "region": "th", "kind": "law", "default": False, "locked": False,
        "name": "ข้อมูลข่าวสารส่วนบุคคลของราชการ",
        "short": "พ.ร.บ.ข้อมูลข่าวสารฯ ม.4, 24",
        "law": "พ.ร.บ.ข้อมูลข่าวสารของราชการ พ.ศ. 2540",
        "sections": "มาตรา 4 (นิยาม \"ข้อมูลข่าวสารส่วนบุคคล\") · มาตรา 24 (ห้ามเปิดเผยโดยไม่ได้รับความยินยอม)",
        "desc": "สำหรับหน่วยงานรัฐ: การศึกษา ฐานะการเงิน ประวัติสุขภาพ ประวัติอาชญากรรม ประวัติการทำงาน "
                "ชื่อ เลขประจำตัว ลายพิมพ์นิ้วมือ และสิ่งที่ระบุตัวบุคคลได้",
        "types": ["PERSON", "TH_ID_CARD", "OTHER_ID", "ADDRESS", "PHONE", "BANK_ACCOUNT", "SALARY",
                  "HEALTH", "CRIMINAL_RECORD", "BIOMETRIC"],
    },
    {
        "id": "hipaa", "region": "intl", "kind": "law", "default": False, "locked": False,
        "name": "HIPAA Safe Harbor (18 ตัวระบุ)",
        "short": "HIPAA §164.514",
        "law": "HIPAA Privacy Rule (สหรัฐอเมริกา)",
        "sections": "45 CFR §164.514(b)(2) วิธี Safe Harbor: ลบตัวระบุ 18 ประเภทเพื่อให้ข้อมูลสุขภาพเป็นข้อมูลนิรนาม",
        "desc": "ใช้เมื่อต้องส่งข้อมูลผู้ป่วยให้คู่ค้าหรือหน่วยงานต่างประเทศ ครอบคลุมชื่อ ที่อยู่ วันที่ เบอร์โทร อีเมล "
                "เลขเวชระเบียน เลขประกัน เลขบัญชี ใบอนุญาต ทะเบียนรถ เลขอุปกรณ์ URL IP ข้อมูลชีวภาพ และเลขระบุอื่น "
                "(ตัวข้อมูลสุขภาพเองไม่ถูกลบในวิธีนี้)",
        "types": ["PERSON", "ADDRESS", "GPS_LOCATION", "DATE_OF_BIRTH", "PHONE", "EMAIL", "TH_ID_CARD", "PASSPORT",
                  "MEDICAL_RECORD", "INSURANCE_ID", "BANK_ACCOUNT", "CREDIT_CARD", "LICENSE_NUMBER",
                  "TH_LICENSE_PLATE", "DEVICE_ID", "URL", "IP_ADDRESS", "BIOMETRIC", "OTHER_ID"],
    },
    {
        "id": "gdpr", "region": "intl", "kind": "law", "default": False, "locked": False,
        "name": "GDPR (สหภาพยุโรป)",
        "short": "GDPR Art.4, 9, 10",
        "law": "Regulation (EU) 2016/679 (GDPR)",
        "sections": "Art. 4(1) personal data · Art. 9 special categories · Art. 10 criminal convictions",
        "desc": "ใช้เมื่อเอกสารมีข้อมูลของบุคคลในสหภาพยุโรป ครอบคลุมตัวระบุทั่วไป ตัวระบุออนไลน์ ข้อมูลประเภทพิเศษ และประวัติคดีอาญา",
        "types": _IDENTIFIERS + _SENSITIVE,
    },
    {
        "id": "iso_privacy", "region": "intl", "kind": "standard", "default": False, "locked": False,
        "name": "ISO/IEC 27701 · 29100 (PII)",
        "short": "ISO/IEC 27701",
        "law": "ISO/IEC 29100:2011 (Privacy framework) · ISO/IEC 27701:2019 (Privacy information management)",
        "sections": "นิยาม PII และ sensitive PII ตาม ISO/IEC 29100 · การควบคุมของ PIMS ตาม ISO/IEC 27701",
        "desc": "สำหรับองค์กรที่ขอหรือรักษาการรับรอง ISO/IEC 27701 ครอบคลุมตัวระบุบุคคลทั้งหมดและ PII ประเภทอ่อนไหว",
        "types": _IDENTIFIERS + _SENSITIVE,
    },
    {
        "id": "internal", "region": "th", "kind": "policy", "default": True, "locked": False,
        "name": "ข้อมูลองค์กรและคู่สัญญา",
        "short": "นโยบายภายใน / NDA",
        "law": "นโยบายความลับขององค์กร / สัญญาไม่เปิดเผยข้อมูล (NDA) ไม่ใช่ข้อบังคับตามกฎหมาย",
        "sections": "",
        "desc": "ชื่อบริษัท ลูกค้า และคู่สัญญา ปิดชุดนี้ได้ถ้าต้องการเก็บชื่อองค์กรไว้ในเอกสาร",
        "types": ["ORGANIZATION"],
    },
]
PACK_IDS = [p["id"] for p in PACKS]
DEFAULT_PACKS = [p["id"] for p in PACKS if p["default"]]
_BY_ID = {p["id"]: p for p in PACKS}


def clean_pack_ids(ids) -> list[str]:
    """Known ids only, in catalogue order; locked packs always included."""
    want = set(ids or [])
    return [p["id"] for p in PACKS if p["id"] in want or p["locked"]]


def pack_types(packs_on) -> list[str]:
    """Entity types switched on by the given packs (+ locked packs + SECRET), in priority order."""
    on: set[str] = {"SECRET"}
    for pid in clean_pack_ids(packs_on):
        on.update(_BY_ID[pid]["types"])
    return [t for t in ENTITY_TYPES if t in on]


def effective_types(packs_on, types_off) -> list[str]:
    off = set(types_off or []) - {"SECRET"}
    return [t for t in pack_types(packs_on) if t not in off]


def law_map(pack_ids=None) -> dict[str, list[str]]:
    """type -> short citations of the packs that cover it: every pack, or only the given ones."""
    use = None if pack_ids is None else set(clean_pack_ids(pack_ids))
    out: dict[str, list[str]] = {t: [] for t in ENTITY_TYPES}
    for p in PACKS:
        if use is None or p["id"] in use:
            for t in p["types"]:
                out[t].append(p["short"])
    return out


def get_pack(pid: str) -> dict | None:
    return _BY_ID.get(pid)


def public_packs() -> list[dict]:
    return [dict(p, types=list(p["types"])) for p in PACKS]


# ---------------------------------------------------------------- presets: one click picks the packs for a kind of organisation
PRESETS = [
    {"id": "default", "name": "ค่าแนะนำ", "desc": "องค์กรทั่วไปในไทย: PDPA ครบทั้งทั่วไปและอ่อนไหว สุขภาพ บัตรชำระเงิน การเงิน และข้อมูลองค์กร",
     "packs": DEFAULT_PACKS},
    {"id": "hospital", "name": "โรงพยาบาล / สุขภาพ", "desc": "เวชระเบียนและข้อมูลผู้ป่วย: PDPA ข้อมูลอ่อนไหว พ.ร.บ.สุขภาพแห่งชาติ และบัตรชำระเงิน",
     "packs": ["pdpa_general", "pdpa_sensitive", "th_health", "th_cyber", "pci_dss", "internal"]},
    {"id": "research", "name": "งานวิจัย / เปิดเผยข้อมูลสุขภาพ", "desc": "ทำข้อมูลนิรนามก่อนส่งต่อหรือเผยแพร่ เพิ่ม HIPAA Safe Harbor ที่ตัดวันที่ URL และตัวระบุ 18 ประเภท",
     "packs": ["pdpa_general", "pdpa_sensitive", "th_health", "th_cyber", "hipaa", "internal"]},
    {"id": "finance", "name": "ธนาคาร / การเงิน / ประกัน", "desc": "ความลับลูกค้า ข้อมูลเครดิต เลขบัตรและ CVV ตาม PCI DSS",
     "packs": ["pdpa_general", "pdpa_sensitive", "th_cyber", "pci_dss", "th_finance", "internal"]},
    {"id": "government", "name": "หน่วยงานรัฐ", "desc": "PDPA ร่วมกับ พ.ร.บ.ข้อมูลข่าวสารของราชการ (ข้อมูลข่าวสารส่วนบุคคล)",
     "packs": ["pdpa_general", "pdpa_sensitive", "th_health", "th_cyber", "th_gov_info", "internal"]},
    {"id": "global", "name": "บริษัทข้ามชาติ / ลูกค้าต่างประเทศ", "desc": "PDPA ร่วมกับ GDPR และ ISO/IEC 27701 สำหรับข้อมูลที่ข้ามพรมแดน",
     "packs": ["pdpa_general", "pdpa_sensitive", "th_health", "th_cyber", "pci_dss", "th_finance", "gdpr", "iso_privacy", "internal"]},
    {"id": "all", "name": "ครบทุกชุด", "desc": "เปิดทุกกฎหมายและมาตรฐาน จับได้มากที่สุด", "packs": PACK_IDS},
]
for _p in PRESETS:
    _p["packs"] = clean_pack_ids(_p["packs"])


def preset_of(pack_ids) -> str:
    """Id of the preset whose packs match exactly, else ""."""
    want = clean_pack_ids(pack_ids)
    return next((p["id"] for p in PRESETS if p["packs"] == want), "")
