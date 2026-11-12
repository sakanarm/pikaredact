"""PikaRedact web app: scan -> review -> highlight or redact -> download.

Run:  uvicorn app.main:app --host 127.0.0.1 --port 8020 [--root-path /PikaRedact]
Files and results live in memory only and expire after JOB_TTL_SECONDS. Statistics
keep counts (never values). LLM base URLs and keys stay on the server.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import config
from .detect import gazetteer
from .detect.llm import Profile, get_profile, list_models, test_connection
from .docio import SUPPORTED, find_leaks, open_document
from .engine import LAYER_LABELS, analyzer, detect, layer_of, models_of, primary_layer
from .models import ENTITY_TYPES, MODES, Finding
from .redact.operators import REMOVED, findings_csv, mask_value, plan_redaction

VAR = config.ROOT / "var"
VAR.mkdir(exist_ok=True)
SETTINGS_FILE = VAR / "settings.json"
STATS_DB = VAR / "stats.db"
STATIC = config.ROOT / "static"

app = FastAPI(title="PikaRedact", docs_url="/api/docs", openapi_url="/api/openapi.json")
app.mount("/static", StaticFiles(directory=STATIC), name="static")
_pool = ThreadPoolExecutor(max_workers=int(os.environ.get("SCAN_WORKERS", "2")))


# ---------------------------------------------------------------- jobs
@dataclass
class Job:
    id: str
    filename: str
    doc: object
    created: float = field(default_factory=time.time)
    status: str = "queued"          # queued | running | done | error
    stage: str = ""
    done: int = 0
    total: int = 0
    error: str = ""
    result: object = None
    options: dict = field(default_factory=dict)
    files: dict = field(default_factory=dict)   # name -> (bytes, media type)
    seconds: float = 0.0


JOBS: dict[str, Job] = {}
_lock = threading.Lock()


def _gc():
    now = time.time()
    with _lock:
        for k in [k for k, j in JOBS.items() if now - j.created > config.JOB_TTL_SECONDS]:
            JOBS.pop(k, None)


def _job(job_id: str) -> Job:
    _gc()
    j = JOBS.get(job_id)
    if not j:
        raise HTTPException(404, "ไม่พบงานนี้ (อาจหมดอายุแล้ว — อัปโหลดใหม่อีกครั้ง)")
    return j


# ---------------------------------------------------------------- settings & stats
DEFAULT_SETTINGS = {"profiles": [], "combine": "union", "min_agree": 2, "mode": "token", "sanitize": True, "use_llm": False}


def load_settings() -> dict:
    s = dict(DEFAULT_SETTINGS)
    if not s["profiles"] and config.default_llm_profile():
        p = config.llm_profiles()[config.default_llm_profile()]
        s["profiles"] = [{"name": p["name"], "model": p["model"]}]
    try:
        s.update(json.loads(SETTINGS_FILE.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    return s


def _db():
    con = sqlite3.connect(STATS_DB)
    con.execute("""create table if not exists events(ts real, kind text, ext text, seconds real, findings int,
                   by_type text, by_layer text, models text, action text, mode text, leaks int)""")
    return con


def _record(**kw):
    try:
        with _db() as con:
            con.execute("insert into events values(?,?,?,?,?,?,?,?,?,?,?)",
                        (time.time(), kw.get("kind"), kw.get("ext"), kw.get("seconds", 0), kw.get("findings", 0),
                         json.dumps(kw.get("by_type", {})), json.dumps(kw.get("by_layer", {})), json.dumps(kw.get("models", [])),
                         kw.get("action", ""), kw.get("mode", ""), kw.get("leaks", 0)))
    except sqlite3.Error:
        pass


# ---------------------------------------------------------------- startup
@app.on_event("startup")
def _warm():
    threading.Thread(target=analyzer, daemon=True).start()  # load Thai NLP once, off the request path


# ---------------------------------------------------------------- pages
@app.get("/", include_in_schema=False)
def landing():
    return FileResponse(STATIC / "index.html")


@app.get("/app", include_in_schema=False)
def app_page():
    return FileResponse(STATIC / "app.html")


@app.get("/health")
def health():
    return {"ok": True, "jobs": len(JOBS)}


# ---------------------------------------------------------------- config / settings / llm
def _profiles_public() -> list[dict]:
    return [{"name": p["name"], "model": p["model"]} for p in config.llm_profiles().values()]


@app.get("/api/config")
def api_config():
    return {
        "entity_types": ENTITY_TYPES, "modes": MODES, "layers": LAYER_LABELS, "supported": SUPPORTED,
        "max_upload_mb": config.MAX_UPLOAD_MB, "review_score": config.REVIEW_SCORE,
        "profiles": _profiles_public(), "settings": load_settings(), "ttl_minutes": config.JOB_TTL_SECONDS // 60,
        "lists": {k: v[1] for k, v in gazetteer.LIST_FILES.items()},
    }


@app.get("/api/settings")
def get_settings():
    return load_settings()


@app.put("/api/settings")
async def put_settings(request: Request):
    body = await request.json()
    known = {p["name"] for p in _profiles_public()}
    s = load_settings()
    if "profiles" in body:
        s["profiles"] = [{"name": str(p.get("name", "")).lower(), "model": str(p.get("model", ""))[:200]}
                         for p in body["profiles"] if str(p.get("name", "")).lower() in known][:6]
    if body.get("combine") in ("union", "vote"):
        s["combine"] = body["combine"]
    if "min_agree" in body:
        s["min_agree"] = max(2, min(6, int(body["min_agree"])))
    if body.get("mode") in MODES:
        s["mode"] = body["mode"]
    for k in ("sanitize", "use_llm"):
        if k in body:
            s[k] = bool(body[k])
    SETTINGS_FILE.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")
    return s


def _profile_or_404(name: str, model: str | None = None) -> Profile:
    p = get_profile(name, model or None)
    if not p:
        raise HTTPException(404, "ไม่พบ connector นี้ในการตั้งค่าเซิร์ฟเวอร์")
    return p


@app.get("/api/llm/models")
def api_models(profile: str):
    p = _profile_or_404(profile)
    try:
        return {"models": list_models(p)}
    except Exception as e:
        raise HTTPException(502, f"ดึงรายชื่อโมเดลไม่ได้ ({type(e).__name__})")


@app.post("/api/llm/test")
async def api_test(request: Request):
    body = await request.json()
    p = _profile_or_404(body.get("profile", ""), body.get("model"))
    try:
        return test_connection(p)
    except Exception as e:
        return {"ok": False, "error": type(e).__name__}


# ---------------------------------------------------------------- lists
@app.get("/api/lists/{name}")
def get_list(name: str):
    if name not in gazetteer.LIST_FILES:
        raise HTTPException(404)
    return {"name": name, "content": gazetteer.read_list(name)}


@app.put("/api/lists/{name}")
async def put_list(name: str, request: Request):
    if name not in gazetteer.LIST_FILES:
        raise HTTPException(404)
    body = await request.json()
    gazetteer.write_list(name, str(body.get("content", ""))[:500_000])
    return {"ok": True}


# ---------------------------------------------------------------- scan
def _profiles_for(opts: dict) -> list[Profile]:
    out = []
    for p in opts.get("profiles") or []:
        prof = get_profile(p.get("name"), p.get("model") or None)
        if prof:
            out.append(prof)
    return out


def _run(job: Job):
    job.status, t0 = "running", time.perf_counter()

    def prog(stage, done=0, total=0):
        job.stage, job.done, job.total = stage, done, total

    try:
        opts = job.options
        res = detect(job.doc.segments, profiles=_profiles_for(opts) if opts.get("use_llm") else None,
                     combine=opts.get("combine", "union"), min_agree=int(opts.get("min_agree", 2)), progress=prog)
        job.result = res
        job.status = "done"
        job.seconds = time.perf_counter() - t0
        _record(kind="scan", ext=job.doc.ext, seconds=job.seconds, findings=len(res.findings),
                by_type=dict(Counter(f.entity_type for f in res.findings)),
                by_layer=dict(Counter(primary_layer(f) for f in res.findings)), models=res.llm_used)
    except Exception as e:  # keep the message generic: it may echo document text
        job.status, job.error = "error", f"ตรวจไม่สำเร็จ ({type(e).__name__})"


@app.post("/api/jobs")
async def create_job(file: UploadFile | None = File(None), text: str = Form(""), options: str = Form("{}")):
    _gc()
    try:
        opts = json.loads(options or "{}")
    except ValueError:
        opts = {}
    if file is not None and file.filename:
        data = await file.read()
        if len(data) > config.MAX_UPLOAD_MB * 1024 * 1024:
            raise HTTPException(413, f"ไฟล์ใหญ่เกิน {config.MAX_UPLOAD_MB} MB")
        name = os.path.basename(file.filename)
    elif text.strip():
        data, name = text.encode("utf-8"), "ข้อความ.txt"
    else:
        raise HTTPException(400, "กรุณาเลือกไฟล์หรือวางข้อความ")
    try:
        doc = open_document(name, data)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(400, f"เปิดไฟล์ไม่ได้ ({type(e).__name__}) — ไฟล์อาจเสียหรือมีรหัสผ่าน")
    s = load_settings()
    merged = {k: opts.get(k, s[k]) for k in ("profiles", "combine", "min_agree", "use_llm")}
    job = Job(uuid.uuid4().hex, name, doc, options=merged)
    with _lock:
        JOBS[job.id] = job
    _pool.submit(_run, job)
    return {"id": job.id}


def _finding_json(f: Finding) -> dict:
    d = f.to_json()
    d["layer"] = primary_layer(f)
    d["layers"] = sorted({layer_of(s) for s in f.sources})
    d["models"] = models_of(f)
    return d


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    j = _job(job_id)
    out = {"id": j.id, "filename": j.filename, "status": j.status, "stage": j.stage, "done": j.done, "total": j.total,
           "error": j.error, "kind": j.doc.kind, "ext": j.doc.ext, "highlight_ext": getattr(j.doc, "highlight_ext", j.doc.ext)}
    if j.status == "done":
        r = j.result
        out.update({
            "segments": [s.to_json() for s in j.doc.segments],
            "findings": [_finding_json(f) for f in r.findings],
            "warnings": list(getattr(j.doc, "warnings", [])) + list(r.warnings),
            "timings": r.timings, "models": r.models, "seconds": round(j.seconds, 2),
        })
    return out


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: str):
    with _lock:
        JOBS.pop(job_id, None)
    return {"ok": True}


# ---------------------------------------------------------------- export
def _safe_stem(filename: str, values: list[str]) -> str:
    stem = os.path.splitext(filename)[0]
    low = stem.casefold()
    if any(len(v) >= 3 and v.casefold() in low for v in values):
        return "document"
    return re.sub(r"[\\/:*?\"<>|]+", "_", stem)[:80] or "document"


@app.post("/api/jobs/{job_id}/export")
async def export(job_id: str, request: Request):
    j = _job(job_id)
    if j.status != "done":
        raise HTTPException(409, "ยังตรวจไม่เสร็จ")
    body = await request.json()
    action = body.get("action", "redact")
    accepted_ids = set(body.get("accepted", []))
    seg_text = {s.id: s.text for s in j.doc.segments}
    chosen = [f for f in j.result.findings if f.id in accepted_ids]
    for k, a in enumerate(body.get("added", []) or []):
        sid, s, e = a.get("seg_id"), int(a.get("start", 0)), int(a.get("end", 0))
        et = a.get("entity_type") if a.get("entity_type") in ENTITY_TYPES else "OTHER_ID"
        if sid in seg_text and 0 <= s < e <= len(seg_text[sid]):
            chosen.append(Finding(sid, s, e, seg_text[sid][s:e], et, 1.0, ["manual"], id=f"m{k + 1}"))
    chosen.sort(key=lambda f: (f.seg_id, f.start))
    labels = {s.id: s.label for s in j.doc.segments}
    values = [f.text for f in chosen]
    stem = _safe_stem(j.filename, values)
    t0 = time.perf_counter()
    j.files = {}
    try:
        if action == "highlight":
            spans: dict = {}
            for f in chosen:
                spans.setdefault(f.seg_id, []).append((f.start, f.end))
            data, info = j.doc.render_highlight(spans)
            hext = getattr(j.doc, "highlight_ext", j.doc.ext)
            j.files[f"{stem}.highlight{hext}"] = (data, "application/octet-stream")
            j.files[f"{stem}.findings.csv"] = (findings_csv(chosen, labels, None, True), "text/csv; charset=utf-8")
            leaks, mapping, counts = [], None, Counter(f.entity_type for f in chosen)
        else:
            mode = body.get("mode") if body.get("mode") in MODES else "token"
            type_modes = {k: v for k, v in (body.get("type_modes") or {}).items() if k in ENTITY_TYPES and v in MODES}
            plan = plan_redaction(chosen, mode, type_modes)
            data, info = j.doc.render_redacted(plan.edits, sanitize=bool(body.get("sanitize", True)))
            out_name = f"{stem}.redacted{j.doc.ext}"
            j.files[out_name] = (data, "application/octet-stream")
            j.files[f"{stem}.findings.csv"] = (findings_csv(chosen, labels, plan.replaced, False), "text/csv; charset=utf-8")
            leaks = find_leaks(data, out_name, [(f.entity_type, f.text) for f in chosen])
            mapping = plan.mapping or None   # encrypted in the browser, never stored here
            counts = plan.counts
            action = f"redact:{mode}"
    except Exception as e:
        raise HTTPException(500, f"สร้างไฟล์ไม่สำเร็จ ({type(e).__name__})")
    _record(kind="export", ext=j.doc.ext, seconds=time.perf_counter() - t0, findings=len(chosen),
            by_type=dict(counts), action=action.split(":")[0], mode=action.split(":")[1] if ":" in action else "", leaks=len(leaks),
            by_layer=dict(Counter(primary_layer(f) for f in chosen)))
    warn = [info["warning"]] if info.get("warning") else []
    if info.get("missed"):
        warn.append(f"มี {info['missed']} รายการที่วางในไฟล์ไม่ได้ — ตรวจไฟล์ผลลัพธ์ก่อนส่งต่อ")
    if info.get("not_highlighted"):
        warn.append(f"{info['not_highlighted']} รายการอยู่ในส่วนที่ไฮไลต์ไม่ได้ (เช่น ข้อมูลไฟล์ คอมเมนต์) — ดูในไฟล์ CSV")
    return {
        "files": [{"name": n, "url": f"api/jobs/{j.id}/files/{n}"} for n in j.files],
        "leaks": [{"type": l["type"], "masked": mask_value(l["type"], l["value"]), "where": l["where"]} for l in leaks],
        "mapping": mapping, "warnings": warn, "count": len(chosen),
    }


@app.get("/api/jobs/{job_id}/files/{name}")
def download(job_id: str, name: str):
    j = _job(job_id)
    if name not in j.files:
        raise HTTPException(404)
    data, media = j.files[name]
    from urllib.parse import quote
    return Response(data, media_type=media, headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"})


# ---------------------------------------------------------------- analytics
@app.get("/api/stats")
def stats(days: int = 30):
    since = time.time() - max(1, min(days, 365)) * 86400
    with _db() as con:
        rows = con.execute("select ts, kind, ext, seconds, findings, by_type, by_layer, models, action, mode, leaks from events where ts >= ? order by ts", (since,)).fetchall()
    scans = [r for r in rows if r[1] == "scan"]
    exports = [r for r in rows if r[1] == "export"]
    by_type, by_layer, by_ext, by_mode, by_model, per_day = Counter(), Counter(), Counter(), Counter(), Counter(), Counter()
    for r in scans:
        by_type.update(json.loads(r[5]))
        by_layer.update(json.loads(r[6]))
        by_ext[r[2]] += 1
        for m in json.loads(r[7] or "[]"):
            by_model[m] += 1
        per_day[time.strftime("%Y-%m-%d", time.localtime(r[0]))] += 1
    for r in exports:
        by_mode[r[9] or r[8]] += 1
    secs = sorted(r[3] for r in scans)
    return {
        "scans": len(scans), "exports": len(exports), "findings": sum(r[4] for r in scans),
        "leaks": sum(r[10] for r in exports), "median_seconds": round(secs[len(secs) // 2], 2) if secs else 0,
        "by_type": dict(by_type), "by_layer": dict(by_layer), "by_ext": dict(by_ext), "by_mode": dict(by_mode),
        "by_model": dict(by_model), "per_day": dict(sorted(per_day.items())),
    }


# ---------------------------------------------------------------- Presidio-compatible API (LiteLLM guardrail)
def _analyze(text: str, entities: list[str] | None, threshold: float) -> list[dict]:
    from .models import Segment

    s = load_settings()
    profiles = _profiles_for(s) if s.get("use_llm") else None
    res = detect([Segment("t", text, "text")], profiles=profiles, combine=s["combine"], min_agree=s["min_agree"],
                 entities=[e for e in entities or [] if e in ENTITY_TYPES] or None)
    return [{"entity_type": f.entity_type, "start": f.start, "end": f.end, "score": round(f.score, 3),
             "analysis_explanation": None, "recognition_metadata": {"recognizer_name": ",".join(f.sources)}}
            for f in res.findings if f.score >= threshold]


@app.post("/analyze")
async def presidio_analyze(request: Request):
    body = await request.json()
    return _analyze(str(body.get("text", "")), body.get("entities"), float(body.get("score_threshold") or 0))


@app.post("/anonymize")
async def presidio_anonymize(request: Request):
    body = await request.json()
    text = str(body.get("text", ""))
    results = body.get("analyzer_results") or []
    ops = body.get("anonymizers") or {}
    items, out, pos = [], [], 0
    for r in sorted(results, key=lambda r: r["start"]):
        s, e, et = int(r["start"]), int(r["end"]), r.get("entity_type", "")
        if s < pos or e > len(text):
            continue
        op = ops.get(et) or ops.get("DEFAULT") or {"type": "replace"}
        kind = op.get("type", "replace")
        if kind == "redact":
            new = ""
        elif kind == "mask":
            new = mask_value(et, text[s:e])
        elif kind == "keep":
            new = text[s:e]
        else:
            new = op.get("new_value") or f"<{et}>"
        out.append(text[pos:s])
        start = sum(len(x) for x in out)
        out.append(new)
        items.append({"start": start, "end": start + len(new), "entity_type": et, "text": new, "operator": kind})
        pos = e
    out.append(text[pos:])
    return {"text": "".join(out), "items": items}


@app.exception_handler(HTTPException)
async def _http_err(request: Request, exc: HTTPException):
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)


__all__ = ["app", "REMOVED"]
