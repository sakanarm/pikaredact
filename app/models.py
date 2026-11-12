from __future__ import annotations

from dataclasses import asdict, dataclass, field

# Order = priority when two detections overlap with equal score (earlier wins).
# "sensitive": personal data under PDPA section 26 (ข้อมูลส่วนบุคคลอ่อนไหว).
ENTITY_TYPES: dict[str, dict] = {
    "SECRET": {"label": "รหัสผ่าน / API key / token", "color": "#b91c1c"},
    "TH_ID_CARD": {"label": "เลขบัตรประชาชน", "color": "#c2410c"},
    "CREDIT_CARD": {"label": "เลขบัตรเครดิต", "color": "#a16207"},
    "CARD_SECURITY": {"label": "CVV / วันหมดอายุบัตร", "color": "#92400e"},
    "EMAIL": {"label": "อีเมล", "color": "#0e7490"},
    "PHONE": {"label": "เบอร์โทร", "color": "#0369a1"},
    "BANK_ACCOUNT": {"label": "เลขบัญชีธนาคาร", "color": "#4d7c0f"},
    "PASSPORT": {"label": "เลขพาสปอร์ต", "color": "#15803d"},
    "LICENSE_NUMBER": {"label": "เลขใบขับขี่ / ใบอนุญาต", "color": "#166534"},
    "MEDICAL_RECORD": {"label": "เลขเวชระเบียน (HN/AN)", "color": "#be123c"},
    "INSURANCE_ID": {"label": "เลขกรมธรรม์ / สิทธิประกัน", "color": "#0f766e"},
    "IP_ADDRESS": {"label": "IP address", "color": "#475569"},
    "DEVICE_ID": {"label": "เลขอุปกรณ์ (MAC / IMEI / VIN / ซีเรียล)", "color": "#334155"},
    "GPS_LOCATION": {"label": "พิกัด GPS", "color": "#a21caf"},
    "SOCIAL_MEDIA": {"label": "บัญชีโซเชียล / LINE ID", "color": "#1d4ed8"},
    "DATE_OF_BIRTH": {"label": "วันเกิด", "color": "#9333ea"},
    "TH_LICENSE_PLATE": {"label": "ทะเบียนรถ", "color": "#7c3aed"},
    "SALARY": {"label": "เงินเดือน / รายได้", "color": "#65a30d"},
    "PERSON": {"label": "ชื่อบุคคล", "color": "#db2777"},
    "OTHER_ID": {"label": "รหัสอ้างอิงบุคคลอื่น ๆ", "color": "#64748b"},
    "ADDRESS": {"label": "ที่อยู่", "color": "#b45309"},
    "ORGANIZATION": {"label": "ชื่อองค์กร", "color": "#2563eb"},
    "URL": {"label": "ลิงก์ / URL", "color": "#57534e"},
    "HEALTH": {"label": "ข้อมูลสุขภาพ", "color": "#dc2626", "sensitive": True},
    "DISABILITY": {"label": "ความพิการ", "color": "#ea580c", "sensitive": True},
    "GENETIC": {"label": "ข้อมูลพันธุกรรม", "color": "#9f1239", "sensitive": True},
    "BIOMETRIC": {"label": "ข้อมูลชีวภาพ (ลายนิ้วมือ / ใบหน้า)", "color": "#6d28d9", "sensitive": True},
    "RELIGION": {"label": "ศาสนา / ความเชื่อ", "color": "#0891b2", "sensitive": True},
    "RACE_ETHNICITY": {"label": "เชื้อชาติ / เผ่าพันธุ์", "color": "#0d9488", "sensitive": True},
    "POLITICAL_OPINION": {"label": "ความคิดเห็นทางการเมือง", "color": "#4338ca", "sensitive": True},
    "SEXUAL_BEHAVIOR": {"label": "พฤติกรรมทางเพศ", "color": "#c026d3", "sensitive": True},
    "CRIMINAL_RECORD": {"label": "ประวัติอาชญากรรม", "color": "#7f1d1d", "sensitive": True},
    "TRADE_UNION": {"label": "ข้อมูลสหภาพแรงงาน", "color": "#3f6212", "sensitive": True},
}
TYPE_PRIORITY = {t: i for i, t in enumerate(ENTITY_TYPES)}
SENSITIVE_TYPES = frozenset(t for t, v in ENTITY_TYPES.items() if v.get("sensitive"))

MODES = {
    "remove": "ลบทิ้ง (████)",
    "mask": "ปิดบางส่วน (081-xxx-xx34)",
    "token": "แทนด้วยรหัส คืนค่าได้ ([PERSON_1])",
    "fake": "แทนด้วยข้อมูลปลอม",
}


@dataclass
class Segment:
    """One contiguous run of text in a document, with its location."""

    id: str
    text: str
    label: str            # fine location, e.g. "สไลด์ 3 · ตาราง 1 แถว 2 คอลัมน์ 1"
    group: str = ""       # coarse location for the viewer, e.g. "สไลด์ 3"
    kind: str = "body"    # body|table|header|footer|comment|note|metadata|hidden|deleted|link|chart|field
    cell: tuple | None = None  # (table_key, row, col) when the text sits in a table/sheet cell

    def cells(self) -> list[list]:
        """Every (table_key, row, col) this segment fills; xlsx shared strings can fill several."""
        c = self.cell
        if not c:
            return []
        items = c if isinstance(c, list) else [c]
        return [[str(k), int(r), int(col)] for k, r, col in items]

    def to_json(self) -> dict:
        d = asdict(self)
        d.pop("cell", None)
        d["cells"] = self.cells()
        return d


@dataclass
class Finding:
    seg_id: str
    start: int
    end: int
    text: str
    entity_type: str
    score: float
    sources: list[str] = field(default_factory=list)
    id: str = ""
    accepted: bool = True

    def to_json(self) -> dict:
        return asdict(self)
