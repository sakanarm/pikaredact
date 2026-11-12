"""Rule packs beyond the classic identifiers: PDPA s.26 sensitive data, health/medical records, device and
online identifiers, card security codes and salary.

Three strengths per type, so the review screen shows doubtful hits without ticking them:
  label : value          ("โรคประจำตัว: เบาหวาน")       ~0.75, ticked
  narrative phrase       ("ป่วยเป็นเบาหวาน")             ~0.6,  ticked
  bare term              ("เบาหวาน")                    0.35, shown unticked; Presidio's context
                                                                  enhancer lifts it to 0.7 when a context
                                                                  word (ป่วย, แพทย์, นับถือ ...) is nearby.
Thai has no spaces between words, so Thai terms are matched without word boundaries and are chosen to be
specific enough not to occur inside unrelated words.
"""
from __future__ import annotations

import ipaddress
import re

from .rules import Rule, RuleRecognizer
from .validators import digits, luhn_ok

I = re.IGNORECASE


def _alt(*terms: str) -> str:
    """Longest first, so 'มะเร็งเต้านม' wins over 'มะเร็ง'."""
    return "|".join(sorted(set(terms), key=len, reverse=True))


# value after "label:" - stops at end of line, a column gap, | or ;, or the next "word:" label
_VAL = r"(?P<v>[^\n:：|;]{1,80}?)(?=\s{2,}|\s*(?:$|\n|[|;]|[^\s:：|;]+\s*[:：]))"
_SEP = r"\s*[:：=]\s*"

_EMPTY = re.compile(
    r"(?i)^\s*(?:-+|–|—|\.+|\*+|_+|ไม่มี|ไม่ทราบ|ไม่ระบุ|ปฏิเสธ|ไม่เคย|ไม่|ปกติ|none|nil|no|n/?a|na|null|unknown|normal|negative|-?\s*)\s*\.?$"
)


def _v_value(val, text, s, e):
    """Drop empty / 'none' answers like 'โรคประจำตัว: ไม่มี'."""
    return False if _EMPTY.match(val) or not re.search(r"[0-9A-Za-zก-ฮ]", val) else None


def _label(name: str, labels: str, score: float = 0.75, value: str = _VAL) -> Rule:
    return Rule(name, rf"(?i)(?<![A-Za-z])(?:{labels}){_SEP}{value}", score, group="v", validate=_v_value)


# ---------------------------------------------------------------- health

_DISEASES_TH = (
    "เบาหวาน", "ความดันโลหิตสูง", "ความดันสูง", "ไขมันในเลือดสูง", "มะเร็ง", "มะเร็งเต้านม", "มะเร็งปอด", "มะเร็งตับ",
    "มะเร็งลำไส้", "มะเร็งลำไส้ใหญ่", "มะเร็งปากมดลูก", "มะเร็งต่อมลูกหมาก", "มะเร็งเม็ดเลือดขาว", "มะเร็งกระเพาะอาหาร",
    "มะเร็งรังไข่", "มะเร็งต่อมน้ำเหลือง", "มะเร็งผิวหนัง", "มะเร็งโพรงจมูก", "มะเร็งไทรอยด์", "เนื้องอกในสมอง",
    "เอชไอวี", "ติดเชื้อเอชไอวี", "เอดส์", "วัณโรค", "ไวรัสตับอักเสบบี", "ไวรัสตับอักเสบซี", "ไวรัสตับอักเสบ", "ตับอักเสบ",
    "ตับแข็ง", "โรคหัวใจ", "หัวใจขาดเลือด", "หัวใจล้มเหลว", "หัวใจวาย", "กล้ามเนื้อหัวใจตาย", "หลอดเลือดสมอง",
    "เส้นเลือดในสมองแตก", "เส้นเลือดในสมองตีบ", "อัมพฤกษ์", "อัมพาต", "ไตวาย", "ไตวายเรื้อรัง", "โรคไตเรื้อรัง", "ฟอกไต",
    "ล้างไต", "เคมีบำบัด", "ฉายรังสี", "ซึมเศร้า", "โรคซึมเศร้า", "ไบโพลาร์", "อารมณ์สองขั้ว", "จิตเภท", "โรคจิตเวช",
    "ผู้ป่วยจิตเวช", "โรควิตกกังวล", "แพนิค", "สมาธิสั้น", "ลมชัก", "ลมบ้าหมู", "หอบหืด", "ถุงลมโป่งพอง", "ภูมิแพ้",
    "ไทรอยด์เป็นพิษ", "โรคเกาต์", "อัลไซเมอร์", "พาร์กินสัน", "สมองเสื่อม", "ตั้งครรภ์", "ตั้งท้อง", "แท้งบุตร", "ทำแท้ง",
    "ติดยาเสพติด", "บำบัดยาเสพติด", "ผู้ติดยา", "ติดสุรา", "พิษสุราเรื้อรัง", "ซิฟิลิส", "หนองใน",
    "โรคติดต่อทางเพศสัมพันธ์", "ติดเชื้อโควิด", "ป่วยโควิด", "โรคเรื้อน", "โรคพุ่มพวง", "ธาลัสซีเมีย", "ฮีโมฟีเลีย",
    "กระดูกพรุน", "ข้อเข่าเสื่อม", "รูมาตอยด์", "สะเก็ดเงิน", "ต้อกระจก", "ต้อหิน", "ไมเกรน",
)
_DISEASES_EN = (
    r"diabetes", r"hypertension", r"cancer", r"carcinoma", r"leuka?emia", r"lymphoma", r"HIV[- ]positive",
    r"tuberculosis", r"hepatitis\s*[ABC]?", r"cirrhosis", r"heart\s+(?:disease|failure|attack)",
    r"kidney\s+(?:disease|failure)", r"renal\s+failure", r"dialysis", r"chemotherapy", r"radiotherapy",
    r"major\s+depressive\s+disorder", r"clinical\s+depression", r"bipolar(?:\s+disorder)?", r"schizophrenia",
    r"ADHD", r"PTSD", r"epilepsy", r"asthma", r"COPD", r"Alzheimer'?s(?:\s+disease)?", r"Parkinson'?s(?:\s+disease)?",
    r"dementia", r"pregnan(?:t|cy)", r"syphilis", r"gonorrho?ea", r"thalass(?:a)?emia", r"ha?emophilia",
)
DISEASE = rf"(?:โรค)?(?:{_alt(*_DISEASES_TH)})|\b(?:HIV|AIDS)\b|\b(?i:{_alt(*_DISEASES_EN)})\b"

HEALTH_LABELS = _alt(
    "โรคประจำตัว", "โรคเรื้อรัง", "ประวัติการเจ็บป่วย", "ประวัติเจ็บป่วย", "ประวัติการแพ้ยา", "ประวัติแพ้ยา", "แพ้ยา",
    "ยาที่แพ้", "ประวัติการรักษา", "การวินิจฉัยโรค", "การวินิจฉัย", "ผลการวินิจฉัย", "ผลวินิจฉัย", "วินิจฉัย",
    "ผลการตรวจสุขภาพ", "ผลตรวจสุขภาพ", "ผลการตรวจเลือด", "ผลตรวจเลือด", "ผลตรวจแล็บ", "ผลแล็บ", "อาการสำคัญ",
    "ยาที่ใช้ประจำ", "ยาประจำตัว", "ภาวะสุขภาพ", "ปัญหาสุขภาพ", "หมู่โลหิต", "หมู่เลือด", "กรุ๊ปเลือด",
    r"diagnosis", r"dx", r"chronic\s+diseases?", r"underlying\s+diseases?", r"medical\s+(?:history|conditions?)",
    r"drug\s+allerg(?:y|ies)", r"allerg(?:y|ies)", r"blood\s+(?:type|group)", r"current\s+medications?",
    r"chief\s+complaint", r"health\s+conditions?",
)
HEALTH = [
    _label("health_label", HEALTH_LABELS),
    Rule("blood_type", r"(?i)(?:หมู่โลหิต|หมู่เลือด|กรุ๊ปเลือด|blood\s*(?:type|group))\s*[:：]?\s*"
         r"(?P<v>(?:AB|A|B|O)(?![A-Za-z])\s*(?:Rh\s*)?[+-]?|เอบี|เอ|บี|โอ)", 0.75, group="v"),
    Rule("icd10", r"(?i)ICD-?10\s*(?:code)?\s*[:：]?\s*(?P<v>[A-TV-Z]\d{2}(?:\.\d{1,4})?)(?![A-Za-z0-9])", 0.75, group="v"),
    Rule("health_narrative",
         r"(?i)(?:ป่วยเป็น|ป่วยด้วย|เป็นโรค|มีโรคประจำตัว(?:คือ|เป็น)?|ได้รับการวินิจฉัย(?:ว่า)?(?:เป็น)?|ตรวจพบ(?:ว่า)?(?:เป็น)?|"
         r"เข้ารับการรักษา(?:อาการ|โรค)?|รักษาตัว(?:ด้วย)?(?:โรค)?|diagnosed\s+with|suffer(?:s|ing|ed)?\s+from|history\s+of)\s*"
         rf"(?P<v>{DISEASE})", 0.65, group="v"),
    Rule("disease_term", rf"(?P<v>{DISEASE})", 0.35, group="v"),
]
HEALTH_CTX = ["ป่วย", "โรค", "รักษา", "วินิจฉัย", "แพทย์", "หมอ", "พยาบาล", "โรงพยาบาล", "คลินิก", "อาการ",
              "ใบรับรองแพทย์", "ติดเชื้อ", "ตรวจพบ", "patient", "diagnos", "treatment", "hospital", "clinic", "medical"]

# ---------------------------------------------------------------- religion / belief

_RELIGIONS = _alt("พุทธ", "คริสต์", "อิสลาม", "ฮินดู", "ซิกข์", "ยิว", "เชน", "บาไฮ", "พราหมณ์", "เต๋า", "ขงจื๊อ", "ชินโต")
RELIGION = [
    _label("religion_label", r"ศาสนา|ความเชื่อทางศาสนา|ลัทธิ|religion|religious\s+(?:belief|affiliation)|faith"),
    Rule("religion_narrative", rf"(?:นับถือ|เข้ารีต|เปลี่ยนไปนับถือ|เปลี่ยนศาสนาเป็น)\s*(?P<v>(?:ศาสนา\s*)?(?:{_RELIGIONS})|ผี|บรรพบุรุษ)",
         0.65, group="v"),
    Rule("religion_term",
         rf"(?P<v>ศาสนา\s*(?:{_RELIGIONS})|ชาวพุทธ|พุทธศาสนิกชน|คริสต์ศาสนิกชน|คริสเตียน|คาทอลิก|คาทอลิค|โปรเตสแตนต์|"
         r"มุสลิม|อิสลามิกชน|ไม่นับถือศาสนา|ไม่มีศาสนา|\b(?:Buddhist|Christian|Catholic|Protestant|Muslim|Hindu|Sikh|Jewish|atheist|agnostic)\b)",
         0.35, group="v"),
]
RELIGION_CTX = ["นับถือ", "ศาสนา", "ความเชื่อ", "ศรัทธา", "religion", "faith", "belief"]

# ---------------------------------------------------------------- race / ethnicity

_ETHNIC = _alt("ไทย", "จีน", "ลาว", "เขมร", "กัมพูชา", "พม่า", "เมียนมา", "มอญ", "มลายู", "ญวน", "เวียดนาม", "อินเดีย",
               "ญี่ปุ่น", "เกาหลี", "ไทยเชื้อสายจีน", "ไทใหญ่", "ไทลื้อ", "ไทดำ", "ผู้ไท", "ภูไท", "กะเหรี่ยง", "ม้ง",
               "อาข่า", "ลาหู่", "ลีซู", "ลัวะ", "ขมุ", "มอแกน", "ยุโรป", "แอฟริกัน", "อาหรับ", "ฝรั่ง")
_TRIBES = _alt("กะเหรี่ยง", "ปกาเกอะญอ", "ม้ง", "อิ้วเมี่ยน", "อาข่า", "ลาหู่", "มูเซอ", "ลีซู", "ลีซอ", "ลัวะ",
               "ชาวขมุ", "มลาบรี", "มอแกน", "มอแกลน", "อูรักลาโวยจ", "ชาวเล", "ชาวเขา", "ไทใหญ่", "ไทลื้อ", "ไทดำ", "ผู้ไท", "ภูไท",
               "ชนกลุ่มน้อย", "ชนเผ่าพื้นเมือง")
RACE = [
    _label("race_label", r"เชื้อชาติ|เชื้อสาย|เผ่าพันธุ์|ชาติพันธุ์|race|ethnicity|ethnic\s+(?:group|origin)"),
    Rule("race_inline", rf"(?:เชื้อชาติ|เชื้อสาย|เผ่าพันธุ์|ชาติพันธุ์)\s*(?P<v>{_ETHNIC})", 0.7, group="v"),
    Rule("tribe_term", rf"(?P<v>(?:ชาว|ชนเผ่า|เผ่า|กลุ่มชาติพันธุ์)?(?:{_TRIBES})|\b(?:Hmong|Akha|Lahu|Lisu|Mien|Moken)\b)", 0.35, group="v"),
]
RACE_CTX = ["ชาติพันธุ์", "ชนเผ่า", "เผ่า", "เชื้อสาย", "เชื้อชาติ", "ชาวเขา", "ethnic", "tribe"]

# ---------------------------------------------------------------- political opinion

_PARTY = r"[ก-๙A-Za-z][ก-๙A-Za-z0-9.]{1,30}"
POLITICAL = [
    _label("political_label", r"ความคิดเห็นทางการเมือง|แนวคิดทางการเมือง|อุดมการณ์ทางการเมือง|สังกัดพรรค(?:การเมือง)?|"
           r"พรรคการเมืองที่สังกัด|political\s+(?:opinions?|affiliation|party|views?)|party\s+affiliation"),
    Rule("political_narrative",
         rf"(?P<v>(?:เป็น)?(?:สมาชิก|ผู้สนับสนุน)พรรค\s*{_PARTY}|สนับสนุนพรรค\s*{_PARTY}|ฝักใฝ่(?:พรรค|ฝ่าย|กลุ่ม)\s*{_PARTY}|"
         rf"ลงคะแนน(?:เสียง)?ให้(?:กับ)?พรรค\s*{_PARTY}|เลือกพรรค\s*{_PARTY}|ร่วมชุมนุม(?:ทางการเมือง)?(?:กับ)?(?:กลุ่ม)?\s*{_PARTY}|"
         r"(?:member|supporter)\s+of\s+the\s+[A-Z][A-Za-z ]{1,30}?\s+[Pp]arty|votes?\s+for\s+the\s+[A-Z][A-Za-z ]{1,30}?\s+[Pp]arty)",
         0.55, group="v"),
]
POLITICAL_CTX = ["การเมือง", "พรรค", "เลือกตั้ง", "ชุมนุม", "political", "party", "election"]

# ---------------------------------------------------------------- sexual behaviour / orientation

SEXUAL = [
    _label("sexual_label", r"เพศวิถี|รสนิยมทางเพศ|อัตลักษณ์ทางเพศ|พฤติกรรมทางเพศ|sexual\s+orientation|sexuality|gender\s+identity|sex\s+life"),
    Rule("sexual_term",
         r"(?P<v>รักร่วมเพศ|รักเพศเดียวกัน|ชายรักชาย|หญิงรักหญิง|เกย์|เลสเบี้ยน|ทอมดี้|ไบเซ็กชวล|ไบเซ็กชัวล์|คนข้ามเพศ|"
         r"บุคคลข้ามเพศ|หญิงข้ามเพศ|ชายข้ามเพศ|สาวประเภทสอง|กะเทย|ทรานส์เจนเดอร์|ผ่าตัดแปลงเพศ|แปลงเพศ|"
         r"(?:มี|เคยมี)เพศสัมพันธ์|ถูกล่วงละเมิดทางเพศ|ถูกข่มขืน|ขายบริการทางเพศ|"
         r"\b(?:gay|lesbian|bisexual|homosexual|transgender|LGBTQ?I?A?\+?|sex\s+worker)\b)", 0.35, group="v"),
]
SEXUAL_CTX = ["รสนิยม", "เพศ", "คู่ชีวิต", "แฟน", "สมรส", "เปิดตัว", "sexual", "orientation", "identif", "partner"]

# ---------------------------------------------------------------- criminal record

CRIMINAL = [
    _label("criminal_label", r"ประวัติอาชญากรรม|ประวัติการกระทำความผิด|ประวัติการต้องโทษ|ประวัติต้องโทษ|ประวัติคดีอาญา|"
           r"ประวัติคดี|criminal\s+(?:records?|history|convictions?)|prior\s+convictions?"),
    Rule("criminal_narrative",
         r"(?P<v>(?:เคย)?(?:ต้องโทษ(?:จำคุก)?|ถูกจำคุก|ติดคุก|ถูกคุมขัง|ถูกพิพากษา(?:ให้)?(?:จำคุก|ลงโทษ)?|ถูกดำเนินคดี(?:อาญา)?|"
         r"ถูกจับกุม|ถูกออกหมายจับ|มีหมายจับ|ศาลพิพากษาจำคุก|รอการลงโทษ|รอลงอาญา|พ้นโทษ|ถูกคุมประพฤติ|เป็นผู้ต้องหา|ตกเป็นจำเลย)"
         r"(?:\s*(?:ใน)?(?:คดี|ฐาน|ข้อหา)[ก-๙A-Za-z0-9]{1,40})?|"
         r"\b(?:was|has\s+been)\s+convicted\s+of\s+[a-z ]{3,40}?(?=[.,;\n]|$)|\bserved\s+(?:a\s+)?(?:prison|jail)\s+(?:sentence|time)\b)",
         0.6, group="v"),
    Rule("case_number",
         r"(?P<v>คดี(?:อาญา)?\s*(?:หมายเลข)?(?:ดำ|แดง)\s*(?:ที่)?\s*[ก-๙A-Za-z.]{0,6}\s*\d{1,6}\s*/\s*\d{2,4}|"
         r"หมายจับ(?:ที่|เลขที่)\s*[ก-๙A-Za-z.]{0,6}\s*\d{1,6}\s*/\s*\d{2,4})", 0.6, group="v"),
]
CRIMINAL_CTX = ["คดี", "ศาล", "จำคุก", "ตำรวจ", "ผู้ต้องหา", "จำเลย", "ความผิด", "criminal", "court", "convict"]

# ---------------------------------------------------------------- trade union

UNION = [
    _label("union_label", r"สหภาพแรงงาน|สมาชิกสหภาพ(?:แรงงาน)?|trade\s+union|union\s+membership|labou?r\s+union"),
    Rule("union_role", r"(?P<v>(?:เป็น)?(?:สมาชิก|กรรมการ|ประธาน|รองประธาน|เลขาธิการ|เหรัญญิก|ผู้แทน|ผู้ก่อตั้ง)สหภาพแรงงาน[ก-๙A-Za-z0-9]{0,40}|"
         r"\b(?:member|officer|representative|steward|leader)\s+of\s+(?:the\s+)?[A-Za-z ]{0,40}?(?:trade|labou?r)\s+union\b|\bunion\s+steward\b)",
         0.6, group="v"),
    Rule("union_term", r"(?P<v>สหภาพแรงงาน[ก-๙A-Za-z0-9]{0,40}|\b(?:trade|labou?r)\s+union\b)", 0.35, group="v"),
]
UNION_CTX = ["สมาชิก", "ลูกจ้าง", "พนักงาน", "ชุมนุม", "นัดหยุดงาน", "member", "employee", "strike"]

# ---------------------------------------------------------------- genetic

GENETIC = [
    _label("genetic_label", r"ผล(?:การ)?ตรวจ(?:ทาง)?พันธุกรรม|ผล(?:การ)?ตรวจ\s*(?:DNA|ดีเอ็นเอ|ยีน)|ข้อมูลพันธุกรรม|ข้อมูลทางพันธุกรรม|"
           r"genetic\s+(?:tests?|results?|data|profile)|genotype|DNA\s+(?:tests?|profile|results?)"),
    Rule("gene_variant", r"(?P<v>\bBRCA[12]\b(?:\s*(?:mutation|positive|\+|กลายพันธุ์))?|\brs\d{4,}\b|\bHLA-[A-Z]+\*\d{2}(?::\d{2})*\b|"
         r"\bAPOE\s*[εe]?4\b)", 0.55, group="v"),
    Rule("genetic_term", r"(?P<v>ตรวจ\s*(?:DNA|ดีเอ็นเอ)|ผล\s*(?:DNA|ดีเอ็นเอ)|ตรวจพิสูจน์ความเป็นบิดา|พาหะ(?:ธาลัสซีเมีย|โรค[ก-๙]{2,20})|"
         r"โรคทางพันธุกรรม|ความผิดปกติทางพันธุกรรม|ยีนกลายพันธุ์|\b(?:genetic\s+(?:disorder|mutation|predisposition)|paternity\s+test)\b)",
         0.35, group="v"),
]
GENETIC_CTX = ["พันธุกรรม", "ดีเอ็นเอ", "dna", "ยีน", "gene", "genetic", "พาหะ"]

# ---------------------------------------------------------------- biometric

_BIO_VAL = r"(?P<v>[A-Za-z0-9+/=_-]{16,}|[^\n:：|;]{1,80}?(?=\s{2,}|\s*(?:$|\n|[|;]|[^\s:：|;]+\s*[:：])))"
BIOMETRIC = [
    _label("biometric_label",
           r"ลายนิ้วมือ|ลายพิมพ์นิ้วมือ|ข้อมูลชีวภาพ|ข้อมูลชีวมิติ|แม่แบบใบหน้า|ข้อมูลใบหน้า|ข้อมูลม่านตา|ม่านตา|ลายเส้นเลือด|"
           r"fingerprint\s+(?:template|data|image|scan)|face\s*(?:template|print|encoding|embedding|vector)|iris\s+(?:scan|template|code)|"
           r"biometric(?:\s+(?:data|template|id|hash))?|voice\s*print|palm\s*(?:print|vein)",
           value=_BIO_VAL),
]

# ---------------------------------------------------------------- disability

DISABILITY = [
    _label("disability_label", r"ประเภทความพิการ|ลักษณะความพิการ|สภาพความพิการ|ความพิการ|disability(?:\s+type)?|impairment"),
    Rule("disability_term",
         r"(?P<v>(?:คน|ผู้|(?<!ความ))พิการ(?:ทาง(?:การ)?(?:เห็น|ได้ยิน|สื่อความหมาย|เคลื่อนไหว|ร่างกาย|สติปัญญา|จิตใจ|พฤติกรรม|การเรียนรู้|ออทิสติก))?|"
         r"ทุพพลภาพ|ตาบอด|สายตาเลือนราง|หูหนวก|หูตึง|ออทิสติก|ออทิซึม|ดาวน์ซินโดรม|"
         r"บกพร่องทาง(?:การ)?(?:เห็น|ได้ยิน|สติปัญญา|การเรียนรู้|ร่างกาย|สุขภาพ|พฤติกรรม)|แขนขาด|ขาขาด|ขาเทียม|แขนเทียม|"
         r"ใช้รถเข็น|วีลแชร์|บัตรประจำตัวคนพิการ|\b(?:Down\s+syndrome|autism|autistic|wheelchair\s+user|visually\s+impaired|"
         r"hearing\s+impaired|legally\s+blind|deaf|disabled\s+person|person\s+with\s+(?:a\s+)?disabilit(?:y|ies))\b)",
         0.35, group="v"),
]
DISABILITY_CTX = ["พิการ", "ความพิการ", "บัตรคนพิการ", "เบี้ยความพิการ", "disab", "impair"]

# ---------------------------------------------------------------- record numbers

_ID = r"(?P<v>[A-Za-z]{0,4}[-/ ]?\d[\dA-Za-z/-]{2,24}\d)(?![\dA-Za-z])"
MEDICAL_RECORD = [
    Rule("hn_an", r"(?i)(?:(?<![A-Za-z])(?-i:HN|AN|MRN)|เลข(?:ที่)?\s*(?:HN|AN)|เลขเวชระเบียน|เลขที่เวชระเบียน|เลขประจำตัวผู้ป่วย|"
         r"เลขที่ผู้ป่วย(?:นอก|ใน)?|hospital\s+(?:number|no\.?)|medical\s+record\s+(?:number|no\.?))(?![A-Za-z])\s*[.:：#]?\s*"
         r"(?P<v>\d[\d/-]{2,14}\d|[A-Za-z]{1,3}[-/]?\d[\d/-]{2,14}\d)(?![\dA-Za-z])", 0.8, group="v"),
]
INSURANCE = [
    Rule("policy_no", r"(?i)(?:เลข(?:ที่)?\s*กรมธรรม์(?:ประกัน[ก-๙]{0,12})?|กรมธรรม์(?:ประกัน[ก-๙]{0,12})?\s*(?:เลขที่|หมายเลข)|"
         r"policy\s*(?:no\.?|number|#)|เลข(?:ที่)?\s*(?:บัตร)?ประกันสังคม|เลขผู้ประกันตน|social\s+security\s+(?:no\.?|number)|"
         r"เลขที่สิทธิ(?:การรักษา)?|เลขบัตรทอง|claim\s*(?:no\.?|number|#)|เลข(?:ที่)?เคลม|member(?:ship)?\s+(?:no\.?|number)\s+(?:of\s+)?(?:health|insurance))"
         rf"\s*[.:：#]?\s*{_ID}", 0.8, group="v"),
]
LICENSE = [
    Rule("driver_license",
         r"(?i)(?:(?:เลข(?:ที่)?\s*)?ใบอนุญาตขับ(?:ขี่|รถ)(?:\s*(?:รถ)?(?:ยนต์|จักรยานยนต์)(?:ส่วนบุคคล|สาธารณะ)?)?|(?:เลข(?:ที่)?\s*)?ใบขับขี่|"
         r"driver'?s?\s+licen[cs]e(?:\s*(?:no\.?|number|#))?|driving\s+licen[cs]e(?:\s*(?:no\.?|number|#))?)"
         r"(?:\s*(?:เลขที่|หมายเลข))?\s*[.:：#]?\s*(?P<v>[A-Za-zก-ฮ]{0,3}\s*[-/]?\s*\d[\d -]{4,14}\d)(?!\d)", 0.8, group="v"),
    Rule("pro_license",
         r"(?i)(?:(?:เลข(?:ที่)?\s*)?ใบอนุญาต(?:ประกอบ(?:วิชาชีพ|โรคศิลปะ)[ก-๙]{0,30}|ทำงาน|ประกอบการ)|เลขที่ใบอนุญาต|ใบประกอบวิชาชีพ|"
         r"work\s+permit(?:\s*(?:no\.?|number))?|(?:professional|medical|nursing)\s+licen[cs]e(?:\s*(?:no\.?|number))?)"
         r"(?:\s*(?:เลขที่|หมายเลข))?\s*[.:：#]?\s*(?P<v>[A-Za-zก-ฮ.]{0,4}\s*[-/]?\s*\d[\d/-]{2,14}\d)(?!\d)", 0.75, group="v"),
    Rule("doctor_license", r"(?<![ก-๙A-Za-z])(?P<v>ว\.\s*\d{4,6})(?!\d)", 0.3, group="v"),
]
LICENSE_CTX = ["แพทย์", "นายแพทย์", "แพทย์หญิง", "ใบอนุญาต", "physician", "doctor"]

# ---------------------------------------------------------------- devices


def _v_mac(val, text, s, e):
    h = re.sub(r"[^0-9A-Fa-f]", "", val).lower()
    return False if h in ("000000000000", "ffffffffffff") else None


def _v_imei(val, text, s, e):
    d = digits(val)
    return True if len(d) == 15 and luhn_ok(d) else (0.6 if len(d) in (15, 16) else False)


DEVICE = [
    Rule("mac", r"(?<![0-9A-Fa-f:-])(?P<v>[0-9A-Fa-f]{2}([:-])(?:[0-9A-Fa-f]{2}\2){4}[0-9A-Fa-f]{2})(?![0-9A-Fa-f:-])", 0.6,
         group="v", validate=_v_mac),
    Rule("imei", r"(?i)(?:IMEI\s*(?:no\.?|number|#|เลขที่)?|เลข\s*IMEI)\s*[.:：#]?\s*(?P<v>\d{2}[ -]?\d{6}[ -]?\d{6}[ -]?\d{1,2})(?!\d)",
         0.8, group="v", validate=_v_imei),
    Rule("vin", r"(?i)(?:\bVIN\b|เลข(?:ที่)?\s*(?:ตัวถัง|แชสซี|แชสซีส์|ตัวรถ)|chassis\s*(?:no\.?|number|#)?)\s*[.:：#]?\s*"
         r"(?P<v>[A-HJ-NPR-Z0-9][A-HJ-NPR-Z0-9-]{6,19}[A-HJ-NPR-Z0-9])(?![A-Za-z0-9])", 0.8, group="v"),
    Rule("engine_no", r"(?i)(?:เลข(?:ที่)?\s*เครื่องยนต์|engine\s*(?:no\.?|number|#))\s*[.:：#]?\s*(?P<v>[A-Z0-9][A-Z0-9-]{4,19}[A-Z0-9])(?![A-Za-z0-9])",
         0.75, group="v"),
    Rule("serial", r"(?i)(?:serial\s*(?:no\.?|number|#)|(?<![A-Za-z])S/?N(?![A-Za-z])|หมายเลขเครื่อง|หมายเลขซีเรียล|เลขซีเรียล|ซีเรียลนัมเบอร์|"
         r"device\s*id|udid|android\s*id)\s*[.:：#]\s*(?P<v>[A-Za-z0-9][A-Za-z0-9-]{4,40})(?![A-Za-z0-9-])", 0.6, group="v"),
]

# ---------------------------------------------------------------- location

_LAT = r"-?(?:[1-8]?\d\.\d{4,}|90\.0{4,})"


def _v_latlng(val, text, s, e):
    """0.1234, 0.5678 is a pair of ratios, not a place."""
    a, b = (abs(float(x)) for x in val.split(","))
    return False if a < 1 and b < 1 else None

_LNG = r"-?(?:1[0-7]\d|[1-9]?\d)\.\d{4,}"
GPS = [
    Rule("gps_label", r"(?i)(?:พิกัด(?:\s*GPS)?|ละติจูด|ลองจิจูด|latitude|longitude|\blat\b|\blng\b|\blon\b|\bGPS\b|coordinates?|lat\s*,\s*long?)"
         r"\s*[:：=]?\s*\(?(?P<v>-?\d{1,3}\.\d{3,}(?:\s*[,/ ]\s*-?\d{1,3}\.\d{3,})?)", 0.8, group="v"),
    Rule("latlng_pair", rf"(?<![\d.=])(?P<v>{_LAT}\s*,\s*{_LNG})(?![\d.])", 0.5, group="v", validate=_v_latlng),
    Rule("dms", r"(?P<v>\d{1,2}°\s*\d{1,2}['′]\s*\d{1,2}(?:\.\d+)?[\"″]?\s*[NS],?\s*\d{1,3}°\s*\d{1,2}['′]\s*\d{1,2}(?:\.\d+)?[\"″]?\s*[EW])",
         0.75, group="v"),
    Rule("maps_url", r"(?i)(?P<v>https?://(?:www\.)?(?:google\.[a-z.]+/maps|maps\.google\.[a-z.]+|goo\.gl/maps|maps\.app\.goo\.gl)[^\s\"'<>]*)",
         0.6, group="v"),
]
GPS_CTX = ["พิกัด", "ตำแหน่ง", "แผนที่", "latitude", "gps", "location", "coordinates"]

# ---------------------------------------------------------------- social media


def _v_handle(val, text, s, e):
    line_start = text.rfind("\n", 0, s) + 1
    if not text[line_start:s].strip():  # "@dataclass" at the start of a line = code decorator
        return False
    if val.lower().lstrip("@") in ("media", "import", "property", "override", "param", "return", "returns", "keyframes",
                                   "font", "charset", "page", "staticmethod", "classmethod", "everyone", "here", "channel"):
        return False
    return None


def _v_social(val, text, s, e):
    """'Line: 120' in a log is a line number, not a LINE ID."""
    if _v_value(val, text, s, e) is False:
        return False
    return False if val.strip().isdigit() and len(val.strip()) < 9 else None


_SOCIAL_SITES = r"facebook|fb|instagram|tiktok|twitter|x|linkedin|youtube|threads|pinterest|t"
SOCIAL = [
    Rule("social_url",
         rf"(?i)(?P<v>https?://(?:www\.|m\.|web\.|th\.)?(?:{_SOCIAL_SITES})\.(?:com|net|me)/(?!(?:share|sharer|watch|hashtag|explore|"
         r"policies|help|legal|terms|privacy|login|about|search)\b)[A-Za-z0-9_.@%-]{2,}[^\s\"'<>)\]]*|"
         r"https?://(?:line\.me/(?:ti/p|R/ti/p)/|lin\.ee/)[^\s\"'<>)\]]+)", 0.7, group="v"),
    Rule("line_id", r"(?:LINE(?:\s*ID)?|(?i:line\s*id)|ไลน์(?:\s*ไอดี)?|ไอดีไลน์|Line)\s*[:：]\s*(?P<v>@?[A-Za-z0-9._-]{3,30})(?![A-Za-z0-9._-])",
         0.75, group="v", validate=_v_social),
    Rule("line_th", r"(?:ไลน์|ไอดีไลน์|ไลน์ไอดี)\s*(?P<v>@?[A-Za-z][A-Za-z0-9._-]{2,29})(?![A-Za-z0-9._-])", 0.6, group="v"),
    Rule("social_label",
         r"(?i)(?<![A-Za-z])(?:facebook|fb|เฟซบุ๊ก|เฟซบุ๊ค|เฟสบุ๊ค|เฟสบุ๊ก|instagram|ig|ไอจี|tiktok|ติ๊กต็อก|twitter|"
         r"telegram|wechat|whatsapp|skype|linkedin)(?:\s*(?:id|ไอดี|account|name|ชื่อ))?\s*[:：]\s*"
         r"(?P<v>@?[A-Za-z0-9ก-๙._-][A-Za-z0-9ก-๙._ -]{1,40}?)(?=\s{2,}|\s*(?:$|\n|[|;,]|[^\s:：|;]+\s*[:：]))", 0.7, group="v",
         validate=_v_social),
    Rule("handle", r"(?<![A-Za-z0-9._%+\-/@])(?P<v>@[A-Za-z0-9_][A-Za-z0-9_.]{2,29})(?![A-Za-z0-9_@])(?!\.[A-Za-z]{2,})", 0.35,
         group="v", validate=_v_handle),
]
SOCIAL_CTX = ["ไลน์", "ไอจี", "twitter", "tiktok", "facebook", "instagram", "เฟซบุ๊ก", "ติดต่อ", "ติดตาม", "แอด", "follow"]

# ---------------------------------------------------------------- URL (HIPAA)

URL = [Rule("url", r"(?i)(?P<v>https?://[^\s<>\"'()\[\]{}]+[^\s<>\"'()\[\]{}.,;:!?]|(?<![A-Za-z0-9@./])www\.[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+[^\s<>\"'()\[\]{}]*[^\s<>\"'()\[\]{}.,;:!?])",
            0.3, group="v")]
# a bare URL stays a suggestion; a personal page in context is accepted
URL_CTX = ["ส่วนตัว", "โปรไฟล์", "บล็อก", "เพจ", "profile", "homepage", "personal", "blog", "portfolio"]

# ---------------------------------------------------------------- card security (PCI DSS 3.3)

CARD_SECURITY = [
    Rule("cvv", r"(?i)(?:(?<![A-Za-z])(?:CVV2?|CVC2?|CSC)(?![A-Za-z])|รหัส\s*(?:CVV|CVC)|รหัส(?:ความปลอดภัย)?\s*(?:3|สาม)\s*หลัก(?:หลังบัตร)?|"
         r"รหัสหลังบัตร|เลข(?:3|สาม)\s*หลักหลังบัตร|เลขหลังบัตร|security\s+code|card\s+verification(?:\s+(?:value|code))?)"
         r"\s*[.:：#=]?\s*(?P<v>\d{3,4})(?!\d)", 0.9, group="v"),
    Rule("card_expiry", r"(?i)(?:(?<![A-Za-z])exp(?:iry|iration)?(?:\s*date)?|valid\s+(?:thru|through|until)|good\s+thru|"
         r"วันหมดอายุ(?:บัตร)?|บัตรหมดอายุ|หมดอายุ)\s*[.:：]?\s*(?P<v>(?:0[1-9]|1[0-2])\s*/\s*(?:\d{2}|20\d{2}))(?![\d/])",
         0.6, group="v"),
]
CARD_CTX = ["บัตร", "card", "visa", "master", "credit", "debit", "jcb", "amex"]

# ---------------------------------------------------------------- salary

_AMOUNT = r"(?P<v>\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d{4,9}(?:\.\d{1,2})?|\d+(?:\.\d+)?\s*(?:k|K|แสน|ล้าน|หมื่น))(?![\d,])"
_PRE = r"\s*[.:：=]?\s*(?:(?:ประมาณ|จำนวน|เดือนละ|ปีละ|ละ|approx\.?|THB|฿|บาท)\s*)?"
SALARY = [
    Rule("salary", rf"(?i)(?:เงินเดือน(?!ขั้นต่ำ|เฉลี่ย)(?:ปัจจุบัน|ล่าสุด|สุทธิ|ที่ต้องการ|ที่คาดหวัง|เดิม|ใหม่)?|ค่าจ้าง(?!ขั้นต่ำ)(?:รายเดือน|รายวัน|รายชั่วโมง)?|"
         rf"ฐานเงินเดือน|อัตราเงินเดือน|\b(?:expected\s+|current\s+|monthly\s+|annual\s+|base\s+)?salary\b|\bwages?\b|\bCTC\b){_PRE}{_AMOUNT}",
         0.7, group="v"),
    Rule("income", rf"(?i)(?:รายได้(?:สุทธิ)?(?:ต่อเดือน|ส่วนตัว|ของผู้กู้|ของผู้สมัคร|ของผู้ขอ[ก-๙]{{0,10}})|\b(?:monthly|personal)\s+income\b)"
         rf"{_PRE}{_AMOUNT}", 0.5, group="v"),
]
SALARY_CTX = ["พนักงาน", "ผู้สมัคร", "ลูกจ้าง", "ผู้กู้", "ลูกค้า", "ต่อเดือน", "เดือนละ", "สลิป", "employee", "applicant", "payslip"]

# ---------------------------------------------------------------- IPv6 (adds to IP_ADDRESS)


def _v_ipv6(val, text, s, e):
    try:
        ip = ipaddress.IPv6Address(val.split("/")[0])
    except ValueError:
        return False
    if ip.is_loopback or ip.is_unspecified or val.count(":") < 3:
        return False
    return 0.75 if ip.is_private or ip.is_link_local else None


IPV6 = [Rule("ipv6", r"(?<![0-9A-Fa-f:.])(?P<v>(?:[0-9A-Fa-f]{1,4}:){7}[0-9A-Fa-f]{1,4}|(?:[0-9A-Fa-f]{1,4}:){1,6}:(?:[0-9A-Fa-f]{1,4}:){0,5}[0-9A-Fa-f]{1,4})"
             r"(?:/\d{1,3})?(?![0-9A-Fa-f:.])", 0.6, group="v", validate=_v_ipv6)]

SENSITIVE_SPEC: list[tuple[str, list[Rule], list[str]]] = [
    ("HEALTH", HEALTH, HEALTH_CTX),
    ("RELIGION", RELIGION, RELIGION_CTX),
    ("RACE_ETHNICITY", RACE, RACE_CTX),
    ("POLITICAL_OPINION", POLITICAL, POLITICAL_CTX),
    ("SEXUAL_BEHAVIOR", SEXUAL, SEXUAL_CTX),
    ("CRIMINAL_RECORD", CRIMINAL, CRIMINAL_CTX),
    ("TRADE_UNION", UNION, UNION_CTX),
    ("GENETIC", GENETIC, GENETIC_CTX),
    ("BIOMETRIC", BIOMETRIC, []),
    ("DISABILITY", DISABILITY, DISABILITY_CTX),
    ("MEDICAL_RECORD", MEDICAL_RECORD, []),
    ("INSURANCE_ID", INSURANCE, []),
    ("LICENSE_NUMBER", LICENSE, LICENSE_CTX),
    ("DEVICE_ID", DEVICE, []),
    ("GPS_LOCATION", GPS, GPS_CTX),
    ("SOCIAL_MEDIA", SOCIAL, SOCIAL_CTX),
    ("URL", URL, URL_CTX),
    ("CARD_SECURITY", CARD_SECURITY, CARD_CTX),
    ("SALARY", SALARY, SALARY_CTX),
]


class _IPv6Recognizer(RuleRecognizer):
    """Separate name from th_ip_address so both register; same entity type."""

    def __init__(self):
        super().__init__("IP_ADDRESS", IPV6, None)
        self.name = "th_ip_address_v6"


def build_sensitive_recognizers() -> list:
    return [RuleRecognizer(entity, rules, context or None) for entity, rules, context in SENSITIVE_SPEC] + [_IPv6Recognizer()]
