"""Evidence report for one export: which law / standard packs were applied, what was found and
redacted per type, and the checks run on the output. Counts, types and file hashes only: no value
from the document ever goes into this report, so it can be filed or sent to a DPO / auditor."""
from __future__ import annotations

import hashlib
import time
from collections import Counter
from html import escape

from .models import ENTITY_TYPES, MODES, SENSITIVE_TYPES, Finding
from .packs import get_pack, law_map

_TH_MONTHS = ["ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.", "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."]
_KIND = {"law": "กฎหมาย", "standard": "มาตรฐาน", "policy": "นโยบายองค์กร"}
_LAYERS = [("ner", "ชื่อและที่อยู่ภาษาไทย (NER)"), ("list", "รายชื่อที่รู้จักและคำยกเว้น")]


def th_time(ts: float) -> str:
    """Thai date and time in Bangkok time, Buddhist year: 13 พ.ย. 2569 14:05 น."""
    t = time.gmtime(ts + 7 * 3600)
    return f"{t.tm_mday} {_TH_MONTHS[t.tm_mon - 1]} {t.tm_year + 543} {t.tm_hour:02d}:{t.tm_min:02d} น."


def _label(t: str) -> str:
    return ENTITY_TYPES.get(t, {}).get("label", t)


def _mode_label(m: str) -> str:
    return MODES.get(m, m).split(" (")[0]


def _e(v) -> str:
    return escape(str(v), quote=True)


def build_report(*, job_id: str, stem: str, ext: str, created: float, action: str, mode: str, type_modes: dict,
                 sanitize: bool, options: dict, models: list[dict], system: list[Finding], chosen: list[Finding],
                 leaks: int, outputs: dict[str, bytes], warnings: list[str], has_mapping: bool, stem_changed: bool) -> bytes:
    redact = action == "redact"
    packs = [p for p in (get_pack(pid) for pid in options.get("packs") or []) if p]
    laws = law_map([p["id"] for p in packs])
    checked = list(options.get("entities") or [])

    chosen_ids = {f.id for f in chosen}
    found = Counter(f.entity_type for f in system)
    accepted = Counter(f.entity_type for f in system if f.id in chosen_ids)
    manual = Counter(f.entity_type for f in chosen if "manual" in f.sources)
    types = [t for t in ENTITY_TYPES if found[t] or accepted[t] or manual[t]]
    n_done = sum(accepted.values()) + sum(manual.values())
    n_rejected = sum(found.values()) - sum(accepted.values())

    rows = []
    for t in types:
        m = (type_modes or {}).get(t) or mode
        done = accepted[t] + manual[t]
        law = " · ".join(laws.get(t) or []) or ("เพิ่มเองตอนตรวจทาน" if manual[t] and not found[t] else "–")
        sens = '<span class="tag">อ่อนไหว</span>' if t in SENSITIVE_TYPES else ""
        rows.append(
            f"<tr><td>{_e(_label(t))} {sens}<div class=\"sub mono\">{_e(t)}</div></td><td>{_e(law)}</td>"
            f"<td class=\"n\">{found[t]}</td><td class=\"n\">{accepted[t]}</td><td class=\"n\">{manual[t]}</td>"
            f"<td class=\"n\">{found[t] - accepted[t]}</td><td class=\"n strong\">{done}</td>"
            f"<td>{_e(_mode_label(m)) if redact and done else ('ไฮไลต์' if done else '–')}</td></tr>")
    if not rows:
        rows.append('<tr><td colspan="8" class="muted">ไม่พบข้อมูลส่วนบุคคลในเอกสารนี้ตามชุดกฎที่เลือก</td></tr>')

    not_found = [t for t in checked if t not in types]
    not_checked = [t for t in ENTITY_TYPES if t not in checked]

    pack_rows = ""
    for p in packs:
        kind = _KIND.get(p["kind"], p["kind"]) + (" · เปิดเสมอ" if p["locked"] else "")
        sections = f'<div class="sub">{_e(p["sections"])}</div>' if p["sections"] else ""
        pack_rows += (f'<tr><td><b>{_e(p["name"])}</b><div class="sub">{_e(kind)}</div></td>'
                      f'<td>{_e(p["law"])}{sections}</td><td class="n">{len(p["types"])}</td></tr>')

    layers = ["กฎและ checksum (เปิดเสมอ)"] + [lbl for k, lbl in _LAYERS if (options.get("layers") or {}).get(k, True)]
    ai = [f"{m['model']} (พบ {m.get('found', 0)}{' · ล้มเหลว' if m.get('failed') else ''})" for m in models or []]
    layers.append("AI: " + ", ".join(ai) if ai else "ไม่ใช้ AI")

    hashes = "".join(f"<tr><td class=\"mono\">{_e(n)}</td><td class=\"mono hash\">{hashlib.sha256(d).hexdigest()}</td></tr>"
                     for n, d in outputs.items())

    if redact:
        leak_line = (f'<span class="ok">ผ่าน</span> สแกนไฟล์ผลลัพธ์ซ้ำ ไม่พบค่าที่ปิดบังหลงเหลือ' if not leaks else
                     f'<span class="bad">พบ {leaks} จุด</span> ยังมีค่าที่ควรปิดหลงเหลือในไฟล์ผลลัพธ์ ต้องแก้ก่อนส่งต่อ')
    else:
        leak_line = '<span class="warn">ไม่ได้ปิดบัง</span> ไฟล์ไฮไลต์ยังมีข้อมูลจริง ใช้ภายในเพื่อตรวจทานเท่านั้น'
    checks = [
        ("ค่าหลงเหลือในไฟล์ผลลัพธ์", leak_line),
        ("ข้อมูลแฝงของไฟล์ (metadata)", "ล้างผู้เขียน คอมเมนต์ และประวัติการแก้ไขแล้ว" if sanitize else "ไม่ได้ล้าง"),
        ("ชื่อไฟล์", "เปลี่ยนเป็น document เพราะชื่อเดิมมีข้อมูลที่ปิดบัง" if stem_changed else "ไม่มีข้อมูลที่ปิดบังในชื่อไฟล์"),
    ]
    if redact:
        checks.append(("การคืนค่า", "มีไฟล์กุญแจคืนค่าที่เข้ารหัสในเบราว์เซอร์ ระบบไม่เก็บกุญแจไว้" if has_mapping
                       else "คืนค่าไม่ได้ (ไม่ได้ใช้โหมดแทนด้วยรหัส)"))
    check_rows = "".join(f"<tr><th>{_e(k)}</th><td>{v}</td></tr>" for k, v in checks)
    warn_html = "".join(f"<li>{_e(w)}</li>" for w in warnings)

    title = "รายงานการปิดบังข้อมูลส่วนบุคคล" if redact else "รายงานการตรวจพบข้อมูลส่วนบุคคล"
    now = time.time()
    html = f"""<!doctype html>
<html lang="th"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_e(title)} · {_e(stem)}</title>
<style>
@page {{ size: A4; margin: 16mm 14mm; }}
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: #F6F6F3; color: #18181B; font: 14px/1.6 "Sarabun", "Leelawadee UI", "Noto Sans Thai", Tahoma, sans-serif; }}
main {{ max-width: 920px; margin: 24px auto; background: #FFF; border: 1px solid #E4E4DF; border-radius: 12px; padding: 32px 36px; }}
h1 {{ margin: 0; font-size: 22px; }} h2 {{ margin: 28px 0 10px; font-size: 15.5px; }}
.meta {{ color: #52525B; margin-top: 4px; font-size: 13px; }}
.mono {{ font-family: ui-monospace, Consolas, monospace; font-size: 12px; }}
.tiles {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px; margin-top: 20px; }}
.tile {{ border: 1px solid #E7E7E2; border-radius: 10px; padding: 10px 14px; }}
.tile b {{ display: block; font-size: 22px; font-variant-numeric: tabular-nums; }} .tile span {{ color: #52525B; font-size: 12.5px; }}
table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
th, td {{ text-align: left; vertical-align: top; padding: 7px 8px; border-bottom: 1px solid #EFEFEA; }}
thead th {{ color: #52525B; font-weight: 500; font-size: 12px; border-bottom: 1px solid #DCDCD6; }}
.kv th {{ width: 210px; color: #3F3F46; font-weight: 500; }}
.n {{ text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }} .strong {{ font-weight: 600; }}
.sub {{ color: #71717A; font-size: 12px; }} .muted {{ color: #71717A; }}
.hash {{ word-break: break-all; }}
.tag {{ display: inline-block; font-size: 11px; padding: 0 6px; border-radius: 5px; background: #FEF2F2; color: #991B1B; }}
.ok {{ color: #166534; font-weight: 600; }} .bad {{ color: #991B1B; font-weight: 600; }} .warn {{ color: #92400E; font-weight: 600; }}
.list {{ color: #3F3F46; font-size: 13px; }}
.sign {{ display: grid; grid-template-columns: 1fr 1fr; gap: 28px; margin-top: 36px; font-size: 13px; }}
.sign div {{ border-top: 1px solid #A1A1AA; padding-top: 6px; color: #52525B; }}
.note {{ margin-top: 28px; padding: 12px 14px; background: #F7F7F4; border-radius: 10px; color: #52525B; font-size: 12.5px; }}
.tw {{ overflow-x: auto; }}
@media (max-width: 640px) {{ main {{ margin: 0; border: 0; border-radius: 0; padding: 20px 16px; }} .sign {{ grid-template-columns: 1fr; }} .mono {{ word-break: break-all; }} }}
@media print {{ body {{ background: #FFF; }} main {{ margin: 0; border: 0; padding: 0; max-width: none; }} tr {{ break-inside: avoid; }} }}
</style></head>
<body><main>
<h1>{_e(title)}</h1>
<div class="meta">เลขอ้างอิง <span class="mono">{_e(job_id[:12])}</span> · ไฟล์ <b>{_e(stem)}{_e(ext)}</b>
 · ตรวจเมื่อ {_e(th_time(created))} · สร้างรายงาน {_e(th_time(now))} (เวลาประเทศไทย)</div>

<div class="tiles">
<div class="tile"><b>{n_done}</b><span>{'จุดที่ปิดบัง' if redact else 'จุดที่ไฮไลต์'}</span></div>
<div class="tile"><b>{len(types)}</b><span>ประเภทข้อมูลที่พบ</span></div>
<div class="tile"><b>{len(packs)}</b><span>ชุดกฎที่ใช้</span></div>
<div class="tile"><b>{sum(manual.values())}</b><span>คนเพิ่มเองตอนตรวจทาน</span></div>
<div class="tile"><b>{n_rejected}</b><span>ระบบพบแต่คนตัดออก</span></div>
</div>

<h2>1. ชุดกฎตามกฎหมายและมาตรฐานที่ใช้</h2>
<div class="tw"><table><thead><tr><th>ชุดกฎ</th><th>กฎหมาย / มาตรฐาน / มาตรา</th><th class="n">ประเภทข้อมูล</th></tr></thead>
<tbody>{pack_rows}</tbody></table></div>
<p class="list">ตรวจทั้งหมด {len(checked)} ประเภทข้อมูล{(' · ไม่ได้ตรวจ: ' + _e(', '.join(_label(t) for t in not_checked))) if not_checked else ''}</p>

<h2>2. ผลการตรวจและการ{'ปิดบัง' if redact else 'ไฮไลต์'}ตามประเภทข้อมูล</h2>
<div class="tw"><table><thead><tr><th>ประเภทข้อมูล</th><th>ฐานกฎหมาย / มาตรฐาน</th><th class="n">ระบบพบ</th><th class="n">ยืนยัน</th>
<th class="n">เพิ่มเอง</th><th class="n">ตัดออก</th><th class="n">{'ปิดบัง' if redact else 'ไฮไลต์'}</th><th>วิธี</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table></div>
{('<p class="list">ตรวจแล้วไม่พบ: ' + _e(', '.join(_label(t) for t in not_found)) + '</p>') if not_found else ''}

<h2>3. การตรวจสอบไฟล์ผลลัพธ์</h2>
<div class="tw"><table class="kv"><tbody>{check_rows}</tbody></table></div>
{('<ul class="list">' + warn_html + '</ul>') if warn_html else ''}

<h2>4. วิธีตรวจจับ</h2>
<div class="tw"><table class="kv"><tbody>
<tr><th>ชั้นการตรวจจับ</th><td>{_e(' · '.join(layers))}</td></tr>
<tr><th>{'โหมดปิดบังหลัก' if redact else 'ผลลัพธ์'}</th><td>{_e(_mode_label(mode)) if redact else 'ไฮไลต์ในไฟล์เดิม'}</td></tr>
</tbody></table></div>

<h2>5. ลายนิ้วมือไฟล์ (SHA-256)</h2>
<div class="tw"><table><thead><tr><th>ไฟล์</th><th>SHA-256</th></tr></thead><tbody>{hashes}</tbody></table></div>
<p class="list">ใช้ยืนยันว่าไฟล์ที่ส่งต่อเป็นไฟล์เดียวกับที่รายงานนี้อ้างถึง</p>

<div class="sign"><div>ผู้ตรวจทาน ........................................ วันที่ ..............</div>
<div>ผู้อนุมัติ / DPO ........................................ วันที่ ..............</div></div>

<div class="note">รายงานนี้สร้างโดย PikaRedact และบันทึกเฉพาะจำนวน ประเภท และฐานกฎหมาย ไม่มีค่าข้อมูลส่วนบุคคลจากเอกสาร
การจับคู่ประเภทข้อมูลกับกฎหมายเป็นเครื่องมือช่วยตรวจ ไม่ใช่คำปรึกษาทางกฎหมาย ควรให้ฝ่ายกฎหมายหรือเจ้าหน้าที่คุ้มครองข้อมูลส่วนบุคคล (DPO) ยืนยันก่อนเปิดเผยเอกสาร</div>
</main></body></html>
"""
    return html.encode("utf-8")
