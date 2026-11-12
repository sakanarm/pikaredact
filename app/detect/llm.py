"""Layer 4 — the organisation's own LLM (LiteLLM / any OpenAI-compatible endpoint).

The model reads chunks of the document and returns the exact strings that are
personal data. We never trust offsets from the model: every string is located
again in the source text, so a hallucinated value can never be "found".
Only profiles from .env are used; the browser never sees base URLs or keys.
"""
from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import httpx

from .. import config
from ..models import ENTITY_TYPES, Finding, Segment

LLM_SCORE = 0.65

NON_CHAT = re.compile(r"(?i)(embed|whisper|tts|rerank|moderation|dall-?e|image|vision-encoder|speech|audio|transcri)")

SYSTEM_PROMPT = """You find personal and confidential data in Thai/English IT documents (requirements, test cases, designs, proposals, meeting notes).
Return ONLY JSON: {"items":[{"text":"<exact substring>","type":"<TYPE>"}]}
TYPE is one of: PERSON, ORGANIZATION, ADDRESS, PHONE, EMAIL, TH_ID_CARD, BANK_ACCOUNT, CREDIT_CARD, PASSPORT, DATE_OF_BIRTH, OTHER_ID, SECRET, IP_ADDRESS, TH_LICENSE_PLATE.
Rules:
- "text" must be copied character-for-character from the input. Do not translate, fix spelling, or add titles.
- PERSON: real people's names incl. Thai nicknames (e.g. "พี่ต้น", "น้องเมย์"), English names, signatures. Do NOT include titles like นาย/นาง/คุณ/Mr. Do NOT include job roles ("ผู้จัดการ", "Admin", "Tester").
- ORGANIZATION: client/partner company or agency names. NOT software products, cloud services, standards or well-known vendors used as technology (Microsoft Azure, Oracle, SAP, AWS, Kubernetes).
- OTHER_ID: employee IDs, customer IDs, usernames, member numbers, HN, case numbers tied to a person.
- SECRET: passwords, API keys, tokens, connection strings with credentials.
- ADDRESS: postal addresses of people or offices (house number, road, district, province, postcode).
- Skip placeholders and templates such as "xxx", "<name>", "ชื่อ-นามสกุล", "081-xxx-xxxx".
- When unsure, include it (missing personal data is worse than an extra item).
If nothing is found return {"items":[]}."""

SAMPLE_TEXT = (
    "บันทึกการประชุม: คุณวิภาวรรณ ทองดี (ผู้จัดการโครงการ บริษัท ตัวอย่างดิจิทัล จำกัด) "
    "แจ้งให้ติดต่อพี่เอก เบอร์ 089-555-0142 รหัสพนักงาน EMP-20931 ก่อนเปิดระบบบน Microsoft Azure"
)
SAMPLE_EXPECTED = ["วิภาวรรณ ทองดี", "เอก", "089-555-0142", "EMP-20931"]


@dataclass
class Profile:
    name: str
    base_url: str
    api_key: str
    model: str


def get_profile(name: str | None, model: str | None = None) -> Profile | None:
    profiles = config.llm_profiles()
    name = (name or "").lower()
    if not name or name not in profiles:
        return None
    p = profiles[name]
    return Profile(p["name"], p["base_url"], p["api_key"], model or p["model"])


def _client() -> httpx.Client:
    return httpx.Client(timeout=config.LLM_TIMEOUT, verify=config.LLM_VERIFY_TLS)


def _headers(p: Profile) -> dict:
    h = {"Content-Type": "application/json"}
    if p.api_key:
        h["Authorization"] = f"Bearer {p.api_key}"
    return h


def _url(p: Profile, path: str) -> str:
    return f"{p.base_url}/{path.lstrip('/')}"


def list_models(p: Profile) -> list[dict]:
    with _client() as c:
        r = c.get(_url(p, "models"), headers=_headers(p))
        r.raise_for_status()
        data = r.json().get("data", [])
    out = []
    for m in data:
        mid = str(m.get("id", ""))
        if mid:
            out.append({"id": mid, "chat": not NON_CHAT.search(mid)})
    return sorted(out, key=lambda m: (not m["chat"], m["id"].lower()))


_THINK = re.compile(r"<think>.*?</think>", re.S)


def _parse_items(content: str) -> list[dict]:
    content = _THINK.sub("", content or "").strip()
    content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content)
    start = min([i for i in (content.find("{"), content.find("[")) if i >= 0], default=-1)
    if start < 0:
        return []
    try:
        obj, _ = json.JSONDecoder().raw_decode(content[start:])
    except json.JSONDecodeError:
        return []
    items = obj.get("items", []) if isinstance(obj, dict) else obj
    out = []
    for it in items if isinstance(items, list) else []:
        if isinstance(it, dict) and isinstance(it.get("text"), str):
            t = it["text"].strip()
            et = str(it.get("type", "")).upper().strip()
            if t and et in ENTITY_TYPES:
                out.append({"text": t, "type": et})
    return out


def complete(p: Profile, text: str) -> list[dict]:
    body = {
        "model": p.model,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ],
    }
    with _client() as c:
        r = c.post(_url(p, "chat/completions"), headers=_headers(p), json=body)
        r.raise_for_status()
        msg = r.json()["choices"][0]["message"]
    return _parse_items(msg.get("content") or "")


def test_connection(p: Profile) -> dict:
    t0 = time.perf_counter()
    items = complete(p, SAMPLE_TEXT)
    dt = time.perf_counter() - t0
    got = {i["text"] for i in items}
    hit = sum(1 for e in SAMPLE_EXPECTED if any(e in g or g in e for g in got if len(g) >= 2))
    return {"ok": True, "latency": round(dt, 2), "found": hit, "expected": len(SAMPLE_EXPECTED), "items": len(items)}


def _chunks(segments: list[tuple[Segment, str]], limit: int) -> list[list[tuple[Segment, str]]]:
    out, cur, size = [], [], 0
    for seg, norm in segments:
        if not norm.strip():
            continue
        n = len(norm) + 1
        if cur and size + n > limit:
            out.append(cur)
            cur, size = [], 0
        cur.append((seg, norm))
        size += n
    if cur:
        out.append(cur)
    return out


def _locate(items: list[dict], chunk: list[tuple[Segment, str]]) -> list[Finding]:
    found = []
    for it in items:
        needle = it["text"]
        if len(needle) < 2:
            continue
        for seg, norm in chunk:
            start = 0
            while True:
                i = norm.find(needle, start)
                if i < 0:
                    break
                found.append(Finding(seg.id, i, i + len(needle), seg.text[i:i + len(needle)], it["type"], LLM_SCORE, ["llm"]))
                start = i + len(needle)
    return found


def llm_findings(p: Profile, segments: list[tuple[Segment, str]], progress=None) -> tuple[list[Finding], list[str]]:
    """Returns findings and warnings. A failing chunk never fails the job."""
    chunks = _chunks(segments, config.LLM_CHUNK_CHARS)
    if not chunks:
        return [], []
    warnings: list[str] = []
    done = 0

    def run(chunk):
        text = "\n".join(norm for _, norm in chunk)
        try:
            return _locate(complete(p, text), chunk), None
        except Exception as e:  # network, timeout, bad JSON
            return [], f"{type(e).__name__}"

    out: list[Finding] = []
    with ThreadPoolExecutor(max_workers=max(1, config.LLM_CONCURRENCY)) as ex:
        for res, err in ex.map(run, chunks):
            done += 1
            out.extend(res)
            if err:
                warnings.append(err)
            if progress:
                progress(done, len(chunks))
    msgs = []
    if warnings:
        msgs.append(f"AI ตอบไม่สำเร็จ {len(warnings)} จาก {len(chunks)} ช่วง ({', '.join(sorted(set(warnings)))}) — ช่วงนั้นใช้ผลจากกฎและรายชื่อเท่านั้น")
    return out, msgs
