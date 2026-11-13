"""PikaRedact web app: scan -> review -> highlight or redact -> download.

Run:  uvicorn app.main:app --host 127.0.0.1 --port 8020 [--root-path /PikaRedact]
Files and results live in memory only and expire after JOB_TTL_SECONDS. Statistics
keep counts (never values). LLM base URLs and keys stay on the server.
"""
from __future__ import annotations

import hashlib
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

from fastapi import Body, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import config
from .detect import gazetteer
from .detect.llm import Profile, get_profile, list_models, test_connection
from .docio import SUPPORTED, find_leaks, open_document
from .docio import pages as pageview
from .engine import LAYER_LABELS, analyzer, detect, layer_of, models_of, primary_layer
from .models import ENTITY_TYPES, MODES, Finding
from .packs import DEFAULT_PACKS, PRESETS, clean_pack_ids, effective_types, law_map, public_packs
from .redact.operators import REMOVED, findings_csv, mask_value, plan_redaction
from .report import build_report

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
    files: dict = field(default_factory=dict)   # name -> (bytes, media type), every output made so far
    outputs: dict = field(default_factory=dict)  # "highlight" | "redact" -> export summary (no values, no mapping)
    pages: dict = field(default_factory=dict)    # page-view key -> PDF bytes (source / previews), newest last
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
DEFAULT_SETTINGS = {
    "profiles": [], "combine": "union", "min_agree": 2, "mode": "token", "sanitize": True, "use_llm": False,
    "models": {},                 # connector name -> chosen model
    "default_connector": "",      # connector name or "none"
    "layers": {"ner": True, "list": True},
    "type_modes": {},             # entity type -> mode; missing = follow the mode picked in review
    "packs_on": DEFAULT_PACKS,     # rule packs (law / standard) switched on; locked packs are always on
    "types_off": ["DATE_OF_BIRTH"],
    "default_output": "ask",      # ask | redact | highlight
}


def load_settings() -> dict:
    s = json.loads(json.dumps(DEFAULT_SETTINGS))
    envp = config.llm_profiles()
    s["models"] = {k: p["model"] for k, p in envp.items() if p["model"]}
    default = config.default_llm_profile()
    if default:
        s["profiles"] = [{"name": default, "model": envp[default]["model"]}]
    s["default_connector"] = default or "none"
    try:
        saved = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        if isinstance(saved.get("models"), dict):
            s["models"].update(saved.pop("models"))
        s.update(saved)
    except (OSError, ValueError):
        pass
    s["packs_on"] = clean_pack_ids(s.get("packs_on"))
    return s


def _db():
    con = sqlite3.connect(STATS_DB)
    con.execute("""create table if not exists events(ts real, kind text, ext text, seconds real, findings int,
                   by_type text, by_layer text, models text, action text, mode text, leaks int)""")
    cols = {r[1] for r in con.execute("pragma table_info(events)")}
    if "extra" not in cols:
        con.execute("alter table events add column extra text default '{}'")
    return con


def _record(**kw):
    try:
        with _db() as con:
            con.execute("insert into events values(?,?,?,?,?,?,?,?,?,?,?,?)",
                        (time.time(), kw.get("kind"), kw.get("ext"), kw.get("seconds", 0), kw.get("findings", 0),
                         json.dumps(kw.get("by_type", {})), json.dumps(kw.get("by_layer", {})), json.dumps(kw.get("models", [])),
                         kw.get("action", ""), kw.get("mode", ""), kw.get("leaks", 0), json.dumps(kw.get("extra", {}))))
    except sqlite3.Error:
        pass


# ---------------------------------------------------------------- startup
@app.on_event("startup")
def _warm():
    threading.Thread(target=analyzer, daemon=True).start()  # load Thai NLP once, off the request path


# ---------------------------------------------------------------- pages
def _asset_version() -> str:
    """Cache-buster for static assets: changes whenever any file under static/ changes."""
    newest = max((p.stat().st_mtime_ns for p in STATIC.rglob("*") if p.is_file()), default=0)
    return format(newest // 1_000_000, "x")


def _page(name: str) -> Response:
    html = (STATIC / name).read_text(encoding="utf-8").replace("__V__", _asset_version())
    return Response(html, media_type="text/html; charset=utf-8", headers={"Cache-Control": "no-cache"})


@app.get("/", include_in_schema=False)
def landing():
    return _page("index.html")


@app.get("/app", include_in_schema=False)
def app_page():
    return _page("app.html")


@app.get("/health")
def health():
    return {"ok": True, "jobs": len(JOBS)}


# ---------------------------------------------------------------- config / settings / llm
def _profiles_public() -> list[dict]:
    """Names and env var names only: base URLs and keys never leave the server."""
    return [{"name": p["name"], "model": p["model"], "env_url": f"LLM_{p['name'].upper()}_BASE_URL",
             "env_key": f"LLM_{p['name'].upper()}_API_KEY", "has_key": bool(p["api_key"])}
            for p in config.llm_profiles().values()]


@app.get("/api/config")
def api_config():
    return {
        "entity_types": ENTITY_TYPES, "modes": MODES, "layers": LAYER_LABELS, "supported": SUPPORTED,
        "max_upload_mb": config.MAX_UPLOAD_MB, "review_score": config.REVIEW_SCORE,
        "min_score": config.MIN_SCORE, "auto_accept_score": config.AUTO_ACCEPT_SCORE,
        "llm": {"temperature": 0, "chunk_chars": config.LLM_CHUNK_CHARS, "concurrency": config.LLM_CONCURRENCY,
                "timeout": config.LLM_TIMEOUT, "verify_tls": config.LLM_VERIFY_TLS},
        "profiles": _profiles_public(), "default_profile": config.default_llm_profile(),
        "settings": load_settings(), "ttl_minutes": config.JOB_TTL_SECONDS // 60,
        "lists": {k: v[1] for k, v in gazetteer.LIST_FILES.items()},
        "packs": public_packs(), "law": law_map(), "presets": PRESETS,
        "active_types": _active_types(),
    }


def _active_types(s: dict | None = None) -> list[str]:
    s = s or load_settings()
    return effective_types(s["packs_on"], s.get("types_off"))


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
    if isinstance(body.get("models"), dict):
        s["models"].update({str(k).lower(): str(v)[:200] for k, v in body["models"].items() if str(k).lower() in known})
    if "default_connector" in body:
        d = str(body["default_connector"]).lower()
        if d in known or d == "none":
            s["default_connector"] = d
    if isinstance(body.get("layers"), dict):
        s["layers"] = {k: bool(body["layers"].get(k, True)) for k in ("ner", "list")}
    if isinstance(body.get("type_modes"), dict):
        s["type_modes"] = {k: v for k, v in body["type_modes"].items() if k in ENTITY_TYPES and k != "SECRET" and v in MODES}
    if isinstance(body.get("types_off"), list):
        s["types_off"] = [t for t in body["types_off"] if t in ENTITY_TYPES and t != "SECRET"]
    if isinstance(body.get("packs_on"), list):
        s["packs_on"] = clean_pack_ids(body["packs_on"])
    if body.get("default_output") in ("ask", "redact", "highlight"):
        s["default_output"] = body["default_output"]
    SETTINGS_FILE.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")
    return {**s, "active_types": _active_types(s)}


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
    content = gazetteer.read_list(name)
    count = sum(1 for line in content.splitlines() if line.strip() and not line.lstrip().startswith("#"))
    try:
        updated = gazetteer.list_path(name).stat().st_mtime
    except OSError:
        updated = None
    return {"name": name, "content": content, "count": count, "updated": updated}


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
                     combine=opts.get("combine", "union"), min_agree=int(opts.get("min_agree", 2)),
                     entities=opts.get("entities"), layers=opts.get("layers"), progress=prog)
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
    merged = {k: opts.get(k, s[k]) for k in ("profiles", "combine", "min_agree", "use_llm", "layers", "sanitize")}
    packs = clean_pack_ids(opts["packs"]) if isinstance(opts.get("packs"), list) else s["packs_on"]
    merged["entities"] = effective_types(packs, s.get("types_off"))
    merged["packs"] = packs
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
           "error": j.error, "kind": j.doc.kind, "page_view": pageview.available(j.doc.kind), "ext": j.doc.ext, "highlight_ext": getattr(j.doc, "highlight_ext", j.doc.ext),
           "created": j.created, "expires": j.created + config.JOB_TTL_SECONDS, "options": {
               k: j.options.get(k) for k in ("use_llm", "profiles", "combine", "layers", "sanitize", "packs", "entities")}}
    if j.status == "done":
        r = j.result
        out.update({
            "segments": [s.to_json() for s in j.doc.segments],
            "findings": [_finding_json(f) for f in r.findings],
            "warnings": list(getattr(j.doc, "warnings", [])) + list(r.warnings),
            "timings": r.timings, "models": r.models, "seconds": round(j.seconds, 2),
            "outputs": j.outputs,
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


def _seed(j: Job) -> int:
    return int(j.id[:8], 16)


def _chosen(j: Job, body: dict) -> tuple[list[Finding], int]:
    """Accepted system findings + manual additions, in document order. Returns (findings, manual count)."""
    seg_text = {s.id: s.text for s in j.doc.segments}
    order = {s.id: i for i, s in enumerate(j.doc.segments)}
    accepted_ids = set(body.get("accepted") or [])
    chosen = [f for f in j.result.findings if f.id in accepted_ids]
    manual = 0
    for k, a in enumerate(body.get("added") or []):
        sid = a.get("seg_id")
        try:
            s, e = int(a.get("start", 0)), int(a.get("end", 0))
        except (TypeError, ValueError):
            continue
        et = a.get("entity_type") if a.get("entity_type") in ENTITY_TYPES else "OTHER_ID"
        mid = str(a.get("id") or "")
        if not re.fullmatch(r"m\d{1,6}", mid):
            mid = f"m{k + 1}"
        if sid in seg_text and 0 <= s < e <= len(seg_text[sid]):
            chosen.append(Finding(sid, s, e, seg_text[sid][s:e], et, 1.0, ["manual"], id=mid))
            manual += 1
    chosen.sort(key=lambda f: (order.get(f.seg_id, 0), f.start, -f.end))
    return chosen, manual


def _redact_opts(body: dict) -> tuple[str, dict]:
    mode = body.get("mode") if body.get("mode") in MODES else "token"
    type_modes = {k: v for k, v in (body.get("type_modes") or {}).items() if k in ENTITY_TYPES and v in MODES}
    return mode, type_modes


@app.post("/api/jobs/{job_id}/preview")
async def preview(job_id: str, request: Request):
    """Replacement text for every chosen finding, so the review screen can show the redacted view."""
    j = _job(job_id)
    if j.status != "done":
        raise HTTPException(409, "ยังตรวจไม่เสร็จ")
    body = await request.json()
    chosen, _ = _chosen(j, body)
    mode, type_modes = _redact_opts(body)
    plan = plan_redaction(chosen, mode, type_modes, seed=_seed(j))
    out = {"replaced": plan.replaced}
    if body.get("with_mapping"):   # result page after a reload: rebuild the key without regenerating files
        out["mapping"] = plan.mapping
    return out


@app.post("/api/jobs/{job_id}/export")
async def export(job_id: str, request: Request):
    j = _job(job_id)
    if j.status != "done":
        raise HTTPException(409, "ยังตรวจไม่เสร็จ")
    body = await request.json()
    action = "highlight" if body.get("action") == "highlight" else "redact"
    chosen, manual = _chosen(j, body)
    labels = {s.id: s.label for s in j.doc.segments}
    values = [f.text for f in chosen]
    stem = _safe_stem(j.filename, values)
    sanitize = bool(body.get("sanitize", j.options.get("sanitize", True)))
    t0 = time.perf_counter()
    files: dict = {}
    mode, replaced = "", {}
    try:
        if action == "highlight":
            spans: dict = {}
            for f in chosen:
                spans.setdefault(f.seg_id, []).append((f.start, f.end))
            data, info = j.doc.render_highlight(spans)
            hext = getattr(j.doc, "highlight_ext", j.doc.ext)
            files[f"{stem}.highlight{hext}"] = (data, "application/octet-stream")
            files[f"{stem}.highlight.csv"] = (findings_csv(chosen, labels, None, True), "text/csv; charset=utf-8")
            leaks, mapping, counts = [], None, dict(Counter(f.entity_type for f in chosen))
        else:
            mode, type_modes = _redact_opts(body)
            plan = plan_redaction(chosen, mode, type_modes, seed=_seed(j))
            data, info = j.doc.render_redacted(plan.edits, sanitize=sanitize)
            out_name = f"{stem}.redacted{j.doc.ext}"
            files[out_name] = (data, "application/octet-stream")
            files[f"{stem}.findings.csv"] = (findings_csv(chosen, labels, plan.replaced, False), "text/csv; charset=utf-8")
            leaks = find_leaks(data, out_name, [(f.entity_type, f.text) for f in chosen])
            mapping = plan.mapping or None   # encrypted in the browser, never stored here
            counts = dict(plan.counts)
            replaced = plan.replaced
    except Exception as e:
        raise HTTPException(500, f"สร้างไฟล์ไม่สำเร็จ ({type(e).__name__})")
    system = j.result.findings
    chosen_ids = {f.id for f in chosen}
    rejected = [f for f in system if f.id not in chosen_ids]
    stem_changed = stem == "document" and os.path.splitext(j.filename)[0] != "document"
    try:   # evidence report: counts, types, legal basis and file hashes only
        files[f"{stem}.{'highlight' if action == 'highlight' else 'redacted'}.report.html"] = (build_report(
            job_id=j.id, stem=stem, ext=j.doc.ext, created=j.created, action=action, mode=mode,
            type_modes=body.get("type_modes") or {}, sanitize=sanitize, options=j.options, models=j.result.models,
            system=system, chosen=chosen, leaks=len(leaks), outputs={n: d for n, (d, _) in files.items()},
            warnings=[info["warning"]] if info.get("warning") else [], has_mapping=bool(mapping),
            stem_changed=stem_changed), "text/html; charset=utf-8")
        report_err = ""
    except Exception as e:  # the report is extra: never fail the export over it
        report_err = f"สร้างรายงานหลักฐานไม่สำเร็จ ({type(e).__name__})"
    seconds = time.perf_counter() - t0
    _record(kind="export", ext=j.doc.ext, seconds=seconds, findings=len(chosen), by_type=counts, action=action, mode=mode,
            leaks=len(leaks), by_layer=dict(Counter(primary_layer(f) for f in chosen)),
            models=[m["model"] for m in j.result.models],
            extra={"found": len(system), "accepted_system": len(system) - len(rejected), "manual": manual,
                   "rejected": len(rejected), "rejected_by_type": dict(Counter(f.entity_type for f in rejected)),
                   "found_by_type": dict(Counter(f.entity_type for f in system)),
                   "found_by_layer": dict(Counter(primary_layer(f) for f in system)),
                   "accepted_by_layer": dict(Counter(primary_layer(f) for f in system if f.id in chosen_ids))})

    warn = [info["warning"]] if info.get("warning") else []
    if report_err:
        warn.append(report_err)
    if info.get("missed"):
        warn.append(f"มี {info['missed']} รายการที่วางในไฟล์ไม่ได้ — ตรวจไฟล์ผลลัพธ์ก่อนส่งต่อ")
    if info.get("not_highlighted"):
        warn.append(f"{info['not_highlighted']} รายการอยู่ในส่วนที่ไฮไลต์ไม่ได้ (เช่น ข้อมูลไฟล์ คอมเมนต์) — ดูในไฟล์ CSV")
    for n in j.outputs.get(action, {}).get("names", []):   # a re-export replaces that action's files only
        j.files.pop(n, None)
    j.files.update(files)
    summary = {
        "action": action, "mode": mode, "count": len(chosen), "manual": manual, "found": len(system),
        "counts": counts, "seconds": round(seconds, 2), "names": list(files),
        "files": [{"name": n, "url": f"api/jobs/{j.id}/files/{n}", "size": len(d)} for n, (d, _) in files.items()],
        "leaks": [{"type": l["type"], "masked": mask_value(l["type"], l["value"]), "where": l["where"]} for l in leaks],
        "warnings": warn + list(getattr(j.doc, "warnings", [])),
        "checks": {"values": len({(f.entity_type, f.text) for f in chosen}), "leaks": len(leaks),
                   "stem_changed": stem_changed,
                   "sanitize": sanitize, "secrets": counts.get("SECRET", 0) if action == "redact" else
                   sum(1 for f in chosen if f.entity_type == "SECRET")},
        "replaced": replaced, "has_mapping": bool(mapping),
        "request": {"accepted": sorted(f.id for f in chosen if not f.id.startswith("m")),
                    "added": body.get("added") or [], "mode": mode, "type_modes": body.get("type_modes") or {},
                    "sanitize": sanitize},
    }
    j.outputs[action] = summary
    return dict(summary, mapping=mapping)


# ---------------------------------------------------------------- page view
PAGE_CACHE = 6   # PDFs kept per job (source + a few previews); they go with the job after its TTL


def _page_pdf(j: Job, what: str, body: dict) -> tuple[str, bytes]:
    kind = j.doc.kind
    if what == "source":
        key, build = "source", (lambda: pageview.to_pdf(kind, j.doc.data))
    else:
        chosen, _ = _chosen(j, body)
        sanitize = bool(body.get("sanitize", j.options.get("sanitize", True)))
        mode, type_modes = _redact_opts(body)
        sig = json.dumps([what, [(f.seg_id, f.start, f.end, f.entity_type) for f in chosen], mode, type_modes, sanitize],
                         ensure_ascii=False, sort_keys=True)
        key = what[0] + hashlib.sha1(sig.encode("utf-8")).hexdigest()[:20]
        if what == "highlight":
            spans: dict = {}
            for f in chosen:
                spans.setdefault(f.seg_id, []).append((f.start, f.end))
            build = lambda: pageview.to_pdf(kind, j.doc.render_highlight(spans)[0])  # noqa: E731
        else:
            plan = plan_redaction(chosen, mode, type_modes, seed=_seed(j))
            build = lambda: pageview.to_pdf(kind, j.doc.render_redacted(plan.edits, sanitize=sanitize)[0])  # noqa: E731
    pdf = j.pages.get(key)
    if pdf is None:
        pdf = build()
        j.pages[key] = pdf
        while len(j.pages) > PAGE_CACHE:   # drop the oldest preview, keep the source
            old = next(k for k in j.pages if k != "source")
            j.pages.pop(old)
    return key, pdf


@app.post("/api/jobs/{job_id}/pages")
def pages(job_id: str, body: dict = Body(...)):
    """Document as pages. what = source (with boxes for `items`) | redact | highlight (the file the export would make)."""
    j = _job(job_id)
    if j.status != "done":
        raise HTTPException(409, "ยังตรวจไม่เสร็จ")
    what = body.get("what", "source")
    if what not in ("source", "redact", "highlight"):
        raise HTTPException(400, "what ต้องเป็น source, redact หรือ highlight")
    if not pageview.available(j.doc.kind):
        raise HTTPException(400, "ไฟล์ชนิดนี้แสดงแบบหน้าเอกสารไม่ได้")
    try:
        key, pdf = _page_pdf(j, what, body)
        sizes, total = pageview.page_sizes(pdf)
        out = {"key": key, "pages": sizes, "total": total}
        if what == "source":
            items = [(str(it.get("text", ""))[:300], [str(x)[:40] for x in it.get("ids") or []][:500])
                     for it in (body.get("items") or [])[:3000] if isinstance(it, dict)]
            out["boxes"] = pageview.locate(pdf, items)
    except pageview.PagesUnavailable as e:
        raise HTTPException(503, str(e))
    except Exception as e:  # keep it generic: messages may echo document text
        raise HTTPException(500, f"แสดงหน้าเอกสารไม่สำเร็จ ({type(e).__name__})")
    return out


@app.get("/api/jobs/{job_id}/pages/{key}/{n}.png")
def page_image(job_id: str, key: str, n: int, w: int = 1100):
    j = _job(job_id)
    pdf = j.pages.get(key)
    if pdf is None:
        raise HTTPException(404)
    try:
        png = pageview.page_png(pdf, n, max(400, min(w, 2000)))
    except IndexError:
        raise HTTPException(404)
    return Response(png, media_type="image/png", headers={"Cache-Control": "private, max-age=3600"})


@app.get("/api/jobs/{job_id}/files/{name}")
def download(job_id: str, name: str):
    j = _job(job_id)
    if name not in j.files:
        raise HTTPException(404)
    data, media = j.files[name]
    from urllib.parse import quote
    return Response(data, media_type=media, headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"})


# ---------------------------------------------------------------- analytics
_EVENT_COLS = "ts, kind, ext, seconds, findings, by_type, by_layer, models, action, mode, leaks, extra"


def _events(since: float, until: float, ext: str, model: str) -> list[dict]:
    with _db() as con:
        rows = con.execute(f"select {_EVENT_COLS} from events where ts >= ? and ts < ? order by ts", (since, until)).fetchall()
    out = []
    for r in rows:
        e = dict(zip(_EVENT_COLS.split(", "), r))
        for k in ("by_type", "by_layer", "extra"):
            try:
                e[k] = json.loads(e[k] or "{}")
            except ValueError:
                e[k] = {}
        try:
            e["models"] = json.loads(e["models"] or "[]")
        except ValueError:
            e["models"] = []
        if ext and (e["ext"] or "").lower() != ext.lower():
            continue
        if model == "none" and e["models"]:
            continue
        if model and model != "none" and not any(m.split("/")[0] == model or m == model for m in e["models"]):
            continue
        out.append(e)
    return out


def _summary(events: list[dict]) -> dict:
    scans = [e for e in events if e["kind"] == "scan"]
    exports = [e for e in events if e["kind"] == "export" and "found" in e["extra"]]
    found = sum(e["extra"]["found"] for e in exports)
    acc = sum(e["extra"]["accepted_system"] for e in exports)
    man = sum(e["extra"]["manual"] for e in exports)
    return {"docs": len(scans), "findings": sum(e["findings"] for e in scans),
            "accept_rate": acc / found if found else None,
            "manual_rate": man / (acc + man) if (acc + man) else None}


@app.get("/api/stats")
def stats(days: int = 30, ext: str = "", model: str = ""):
    days = max(1, min(days, 365))
    now = time.time()
    since = now - days * 86400
    cur = _events(since, now + 1, ext, model)
    prev = _events(since - days * 86400, since, ext, model)
    scans = [e for e in cur if e["kind"] == "scan"]
    exports = [e for e in cur if e["kind"] == "export"]
    rich = [e for e in exports if "found" in e["extra"]]

    by_type, by_layer, per_day, per_day_docs = Counter(), Counter(), Counter(), Counter()
    for e in scans:
        by_type.update(e["by_type"])
        by_layer.update(e["by_layer"])
        day = time.strftime("%Y-%m-%d", time.localtime(e["ts"]))
        per_day[day] += e["findings"]
        per_day_docs[day] += 1
    days_list = [time.strftime("%Y-%m-%d", time.localtime(now - (days - 1 - i) * 86400)) for i in range(days)]

    found_by_type, rejected_by_type, found_by_layer, accepted_by_layer = Counter(), Counter(), Counter(), Counter()
    manual = 0
    for e in rich:
        x = e["extra"]
        found_by_type.update(x.get("found_by_type", {}))
        rejected_by_type.update(x.get("rejected_by_type", {}))
        found_by_layer.update(x.get("found_by_layer", {}))
        accepted_by_layer.update(x.get("accepted_by_layer", {}))
        manual += x.get("manual", 0)

    outputs = Counter(f"{e['action']}:{e['mode']}" if e["action"] == "redact" else e["action"] for e in exports)

    per_ext: dict = {}
    for e in scans:
        d = per_ext.setdefault((e["ext"] or "").lstrip(".").upper() or "?", {"docs": 0, "findings": 0, "found": 0, "acc": 0, "manual": 0})
        d["docs"] += 1
        d["findings"] += e["findings"]
    for e in rich:
        d = per_ext.setdefault((e["ext"] or "").lstrip(".").upper() or "?", {"docs": 0, "findings": 0, "found": 0, "acc": 0, "manual": 0})
        d["found"] += e["extra"]["found"]
        d["acc"] += e["extra"]["accepted_system"]
        d["manual"] += e["extra"]["manual"]

    with _db() as con:   # filter choices come from every event, not just the filtered range
        all_rows = con.execute("select ext, models from events").fetchall()
    exts = sorted({(r[0] or "").lstrip(".").lower() for r in all_rows if r[0]})
    models = sorted({m.split("/")[0] for r in all_rows for m in json.loads(r[1] or "[]")})

    secs = sorted(e["seconds"] for e in scans)
    return {
        "days": days, "now": now, "summary": _summary(cur), "previous": _summary(prev),
        "scans": len(scans), "exports": len(exports), "findings": sum(e["findings"] for e in scans),
        "leaks": sum(e["leaks"] for e in exports), "median_seconds": round(secs[len(secs) // 2], 2) if secs else 0,
        "per_day": [{"date": d, "findings": per_day.get(d, 0), "docs": per_day_docs.get(d, 0)} for d in days_list],
        "by_type": dict(by_type), "by_layer": dict(by_layer),
        "found_by_type": dict(found_by_type), "rejected_by_type": dict(rejected_by_type),
        "found_by_layer": dict(found_by_layer), "accepted_by_layer": dict(accepted_by_layer), "manual": manual,
        "outputs": dict(outputs), "per_ext": per_ext, "filters": {"exts": exts, "models": models},
    }


@app.get("/api/leaderboard")
def leaderboard():
    """Offline evaluation results (eval/run_eval.py writes var/leaderboard.json). Empty until it has been run."""
    try:
        return json.loads((VAR / "leaderboard.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"rows": [], "columns": [], "dataset": None}


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
