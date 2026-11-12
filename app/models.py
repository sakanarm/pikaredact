from __future__ import annotations

from dataclasses import asdict, dataclass, field

# Order = priority when two detections overlap with equal score (earlier wins).
ENTITY_TYPES: dict[str, dict] = {
    "SECRET": {"label": "รหัสผ่าน / API key / token", "color": "#b91c1c"},
    "TH_ID_CARD": {"label": "เลขบัตรประชาชน", "color": "#c2410c"},
    "CREDIT_CARD": {"label": "เลขบัตรเครดิต", "color": "#a16207"},
    "EMAIL": {"label": "อีเมล", "color": "#0e7490"},
    "PHONE": {"label": "เบอร์โทร", "color": "#0369a1"},
    "BANK_ACCOUNT": {"label": "เลขบัญชีธนาคาร", "color": "#4d7c0f"},
    "PASSPORT": {"label": "เลขพาสปอร์ต", "color": "#15803d"},
    "IP_ADDRESS": {"label": "IP address", "color": "#475569"},
    "DATE_OF_BIRTH": {"label": "วันเกิด", "color": "#9333ea"},
    "TH_LICENSE_PLATE": {"label": "ทะเบียนรถ", "color": "#7c3aed"},
    "PERSON": {"label": "ชื่อบุคคล", "color": "#db2777"},
    "OTHER_ID": {"label": "รหัสอ้างอิงบุคคลอื่น ๆ", "color": "#64748b"},
    "ADDRESS": {"label": "ที่อยู่", "color": "#b45309"},
    "ORGANIZATION": {"label": "ชื่อองค์กร", "color": "#2563eb"},
}
TYPE_PRIORITY = {t: i for i, t in enumerate(ENTITY_TYPES)}

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

    def to_json(self) -> dict:
        d = asdict(self)
        d.pop("cell", None)
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
