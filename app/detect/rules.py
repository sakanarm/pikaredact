"""Layer 1 — fixed-format patterns with validators, wrapped as Presidio recognizers.

Patterns use ASCII-only character classes for boundaries because Thai text
often touches Latin/digits with no space ("โทร0812345678").
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Optional, Union

from presidio_analyzer import AnalysisExplanation, EntityRecognizer, RecognizerResult

from .validators import card_prefix_ok, digits, luhn_ok, thai_id_checksum_ok

TH = "\u0E00-\u0E7F"

PROVINCES = (
    "กรุงเทพมหานคร กรุงเทพฯ กทม กระบี่ กาญจนบุรี กาฬสินธุ์ กำแพงเพชร ขอนแก่น จันทบุรี ฉะเชิงเทรา ชลบุรี ชัยนาท "
    "ชัยภูมิ ชุมพร เชียงราย เชียงใหม่ ตรัง ตราด ตาก นครนายก นครปฐม นครพนม นครราชสีมา นครศรีธรรมราช นครสวรรค์ "
    "นนทบุรี นราธิวาส น่าน บึงกาฬ บุรีรัมย์ ปทุมธานี ประจวบคีรีขันธ์ ปราจีนบุรี ปัตตานี พระนครศรีอยุธยา พะเยา พังงา "
    "พัทลุง พิจิตร พิษณุโลก เพชรบุรี เพชรบูรณ์ แพร่ ภูเก็ต มหาสารคาม มุกดาหาร แม่ฮ่องสอน ยโสธร ยะลา ร้อยเอ็ด "
    "ระนอง ระยอง ราชบุรี ลพบุรี ลำปาง ลำพูน เลย ศรีสะเกษ สกลนคร สงขลา สตูล สมุทรปราการ สมุทรสงคราม สมุทรสาคร "
    "สระแก้ว สระบุรี สิงห์บุรี สุโขทัย สุพรรณบุรี สุราษฎร์ธานี สุรินทร์ หนองคาย หนองบัวลำภู อ่างทอง อำนาจเจริญ "
    "อุดรธานี อุตรดิตถ์ อุทัยธานี อุบลราชธานี"
).split()
PROVINCE_RE = "|".join(sorted(map(re.escape, PROVINCES), key=len, reverse=True))

Validator = Callable[[str, str, int, int], Union[bool, float, None]]


@dataclass
class Rule:
    name: str
    pattern: str
    score: float
    group: Optional[str] = None          # named group that holds the span to redact
    validate: Optional[Validator] = None  # -> False drop | True 1.0 | float new score | None keep
    flags: int = 0

    def __post_init__(self):
        self.regex = re.compile(self.pattern, self.flags)


class RuleRecognizer(EntityRecognizer):
    def __init__(self, entity: str, rules: list[Rule], context: Optional[list[str]] = None):
        self.rules = rules
        super().__init__(
            supported_entities=[entity],
            name=f"th_{entity.lower()}",
            supported_language="th",
            context=context,
        )

    def load(self) -> None:
        pass

    def analyze(self, text, entities, nlp_artifacts=None):
        out: list[RecognizerResult] = []
        entity = self.supported_entities[0]
        for rule in self.rules:
            for m in rule.regex.finditer(text):
                g = rule.group or 0
                s, e = m.span(g)
                if s < 0 or s == e:
                    continue
                score = rule.score
                if rule.validate:
                    v = rule.validate(m.group(g), text, s, e)
                    if v is False:
                        continue
                    if v is True:
                        score = 1.0
                    elif isinstance(v, float):
                        score = v
                out.append(
                    RecognizerResult(
                        entity_type=entity,
                        start=s,
                        end=e,
                        score=score,
                        analysis_explanation=AnalysisExplanation(f"{self.name}:{rule.name}", score, pattern_name=rule.name),
                        recognition_metadata={
                            RecognizerResult.RECOGNIZER_NAME_KEY: f"{self.name}:{rule.name}",
                            RecognizerResult.RECOGNIZER_IDENTIFIER_KEY: self.id,
                        },
                    )
                )
        return out


# ---------------------------------------------------------------- validators

def _v_thai_id(val, text, s, e):
    if thai_id_checksum_ok(val):
        return True
    # Formatted like an ID (1-2345-67890-12-3) but bad checksum: typo or sample data.
    if re.fullmatch(r"\d[ -]\d{4}[ -]\d{5}[ -]\d{2}[ -]\d", val):
        return 0.45
    return False


def _v_card(val, text, s, e):
    return 0.95 if (luhn_ok(val) and card_prefix_ok(val)) else False


_VERSION_CTX = re.compile(r"(?i)(?:\bv|\bver\.?|\bversion|เวอร์ชัน|เวอร์ชั่น|\brelease|\bbuild|รุ่น)\s*$")


def _v_ip(val, text, s, e):
    if _VERSION_CTX.search(text[max(0, s - 12):s]):
        return False
    ip = val.split("/")[0]
    if ip in ("0.0.0.0", "127.0.0.1", "255.255.255.255") or ip.startswith("255.255."):
        return False
    a, b = (int(x) for x in ip.split(".")[:2])
    if a == 10 or (a == 172 and 16 <= b <= 31) or (a == 192 and b == 168):
        return 0.7  # internal infrastructure address
    return None


_SECRET_STOP = re.compile(
    r"(?i)^(?:\*+|x+|•+|\.+|-+|_+|required|optional|string|text|varchar|nvarchar|char|int|number|null|none|n/?a|"
    r"hidden|masked|encrypted|hash(?:ed)?|bcrypt|sha\d*|md5|yes|no|true|false|field|input|policy|length|reset|"
    r"change|expired?|<.*|\{.*|\$\{.*|\[.*|your_?\w*|xxx\w*|placeholder|example|sample|\.\.\.)"
)


def _v_secret_kv(val, text, s, e):
    if re.search(f"[{TH}]", val) or _SECRET_STOP.match(val):
        return False
    if len(set(val)) <= 2:
        return False
    return None


def _v_secret_strong(val, text, s, e):
    """'pwd P@ssw0rd!2024' (no ':' or '='): accept only values that look like a real password."""
    if _v_secret_kv(val, text, s, e) is False or len(val) < 8:
        return False
    classes = sum(bool(re.search(p, val)) for p in (r"[a-z]", r"[A-Z]", r"\d", r"[^A-Za-z0-9]"))
    return None if classes >= 3 else False


def _v_plate(val, text, s, e):
    if re.search(PROVINCE_RE, val):
        return 0.85
    return 0.35 if len(digits(val)) >= 3 else 0.25


# ---------------------------------------------------------------- rules

_KV_KEYS = (
    r"password|passwd|pwd|passcode|pass|รหัสผ่าน|secret|api[_ -]?key|apikey|access[_ -]?key|secret[_ -]?key|"
    r"client[_ -]?secret|auth[_ -]?token|access[_ -]?token|refresh[_ -]?token|token|accountkey|sharedaccesskey|"
    r"private[_ -]?key"
)

RECOGNIZERS_SPEC: list[tuple[str, list[Rule], list[str]]] = [
    (
        "TH_ID_CARD",
        [Rule("th_id", r"(?<![\d-])\d[ -]?\d{4}[ -]?\d{5}[ -]?\d{2}[ -]?\d(?![\d-])", 0.4, validate=_v_thai_id)],
        ["บัตรประชาชน", "ประจำตัวประชาชน", "บัตรประจำตัว", "เลขบัตร", "citizen", "national", "id card", "idcard", "ปชช"],
    ),
    (
        "PHONE",
        [
            Rule("mobile", r"(?<![\d+])(?:\+?66[ -]?(?:\(0\))?[ -]?|0)[689](?:[ .-]?\d){8}(?:\s*(?:ต่อ|ext\.?|x)\s*\d{1,5})?(?!\d)", 0.75),
            Rule("landline", r"(?<![\d+])(?:\+?66[ -]?(?:\(0\))?[ -]?|0)[2-7](?:[ .-]?\d){7}(?:\s*(?:ต่อ|ext\.?|x)\s*\d{1,5})?(?!\d)", 0.5),
        ],
        ["โทร", "เบอร์", "มือถือ", "tel", "phone", "mobile", "fax", "แฟกซ์", "ติดต่อ", "contact", "call"],
    ),
    (
        "EMAIL",
        [Rule("email", r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9][A-Za-z0-9._%+-]*@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}(?![A-Za-z0-9-])", 0.9)],
        [],
    ),
    (
        "CREDIT_CARD",
        [Rule("card", r"(?<![\d-])(?:\d[ -]?){14,15}\d(?![\d-])", 0.4, validate=_v_card)],
        ["บัตรเครดิต", "บัตรเดบิต", "credit", "debit", "card", "visa", "master", "amex", "jcb"],
    ),
    (
        "BANK_ACCOUNT",
        [
            Rule("acct_3-1-5-1", r"(?<![\d-])\d{3}-\d-\d{5}-\d(?![\d-])", 0.6),
            Rule("acct_3-6-1", r"(?<![\d-])\d{3}-\d{6}-\d(?![\d-])", 0.45),
            Rule("acct_10", r"(?<![\d-])\d{10}(?![\d-])", 0.2),
            Rule("acct_12", r"(?<![\d-])\d{12}(?![\d-])", 0.15),
        ],
        ["บัญชี", "ธนาคาร", "account", "a/c", "acct", "bank", "กสิกร", "ไทยพาณิชย์", "กรุงเทพ", "กรุงไทย", "กรุงศรี",
         "ทหารไทย", "ttb", "scb", "kbank", "bbl", "ktb", "ออมสิน", "พร้อมเพย์", "promptpay", "โอน"],
    ),
    (
        "PASSPORT",
        [Rule("passport", r"(?<![A-Za-z0-9])[A-Z]{1,2}\d{7}(?![A-Za-z0-9])", 0.2)],
        ["passport", "พาสปอร์ต", "หนังสือเดินทาง"],
    ),
    (
        "IP_ADDRESS",
        [Rule("ipv4", r"(?<![\d.])(?:(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.){3}(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(?:/\d{1,2})?(?!\.?\d)", 0.55, validate=_v_ip)],
        ["ip", "server", "host", "เซิร์ฟเวอร์", "gateway", "subnet", "vpn", "firewall", "database", "db"],
    ),
    (
        "SECRET",
        [
            Rule("kv", rf"(?i)(?<![A-Za-z])(?:{_KV_KEYS})\s*[\"']?\s*(?:[:=：]|=>|->|คือ)\s*[\"']?(?P<v>[^\s\"'`,;<>]{{3,}})", 0.85, group="v", validate=_v_secret_kv),
            Rule("kv_space", rf"(?i)(?<![A-Za-z])(?:password|passwd|pwd|passcode|รหัสผ่าน|รหัส)\s+(?P<v>[^\s\"'`,;<>]{{8,}})", 0.7, group="v", validate=_v_secret_strong),
            Rule("url_creds", r"(?i)\b[a-z][a-z0-9+.-]*://[^\s:/@]+:(?P<v>[^\s@/]+)@", 0.9, group="v"),
            Rule("bearer", r"(?i)\bBearer\s+(?P<v>[A-Za-z0-9\-._~+/]{16,}=*)", 0.9, group="v"),
            Rule("aws", r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b", 0.95),
            Rule("github", r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})\b", 0.95),
            Rule("slack", r"\bxox[abposr]-[A-Za-z0-9-]{10,}\b", 0.95),
            Rule("google", r"\bAIza[0-9A-Za-z_\-]{35}\b", 0.95),
            Rule("sk_key", r"\bsk-[A-Za-z0-9_\-]{16,}\b", 0.9),
            Rule("jwt", r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b", 0.9),
            Rule("pem", r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)", 0.95),
        ],
        [],
    ),
    (
        "DATE_OF_BIRTH",
        [Rule(
            "dob",
            r"(?i)(?:วันเกิด|วัน\s*/?\s*เดือน\s*/?\s*ปี\s*เกิด|เกิดวันที่|เกิดเมื่อ|date\s+of\s+birth|birth\s*date|\bd\.?o\.?b\b\.?)"
            r"\s*[:：]?\s*(?:วันที่\s*)?(?P<v>\d{1,2}\s*[/.-]\s*\d{1,2}\s*[/.-]\s*\d{2,4}|\d{4}-\d{2}-\d{2}|\d{1,2}\s*[ก-๙A-Za-z.]+\.?\s*\d{2,4})",
            0.85, group="v")],
        [],
    ),
    (
        "TH_LICENSE_PLATE",
        [Rule("plate", rf"(?<![{TH}\dA-Za-z])(?P<v>\d?[ก-ฮ]{{1,2}}[ -]?\d{{1,4}}(?:\s*(?:{PROVINCE_RE}))?)(?![\d{TH}])", 0.3, group="v", validate=_v_plate)],
        ["ทะเบียน", "ป้าย", "รถ", "license plate", "plate"],
    ),
    (
        "ADDRESS",
        [Rule(
            "th_address",
            r"(?P<v>(?:(?:บ้าน)?เลขที่\s*\d{1,5}(?:/\d{1,5})?|\d{1,5}/\d{1,5}|\d{1,5}(?=\s)|หมู่(?:ที่)?\s*\d+|ม\.\s*\d+|ซอย|ซ\.|ถนน|ถ\.)"
            r"[^\n]{0,120}?(?:แขวง|ตำบล|ต\.|เขต|อำเภอ|อ\.)\s*[^\n]{1,80}?"
            rf"(?:จังหวัด|จ\.|{PROVINCE_RE})\s*[^\n]{{0,30}}?(?<!\d)[1-9]\d{{4}})(?!\d)",
            0.6, group="v")],
        [],
    ),
    (
        "ORGANIZATION",
        [
            Rule("th_company", r"(?P<v>(?:บริษัท|บจก\.|บมจ\.|ห้างหุ้นส่วนจำกัด|หจก\.)\s*[^\n,()]{1,60}?\s*จำกัด(?:\s*\(มหาชน\))?)", 0.6, group="v"),
            Rule("en_company",
                 r"(?P<v>\b[A-Z][A-Za-z0-9&.'-]*(?:\s+[A-Z&][A-Za-z0-9&.'-]*){0,5},?\s+(?:Co\.,?\s*Ltd\.?|Company\s+Limited|Public\s+Company\s+Limited|PCL\b\.?|Corporation|Corp\.|Inc\.|Ltd\.|LLC\b))",
                 0.55, group="v"),
        ],
        [],
    ),
]


def build_rule_recognizers() -> list[EntityRecognizer]:
    return [RuleRecognizer(entity, rules, context or None) for entity, rules, context in RECOGNIZERS_SPEC]
