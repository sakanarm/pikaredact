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
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from . import auth, config
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
    owner: int = 0                               # user id; only the owner can see the job, admins included


JOBS: dict[str, Job] = {}
_lock = threading.Lock()


def _gc():
    now = time.time()
    with _lock:
        for k in [k for k, j in JOBS.items() if now - j.created > config.JOB_TTL_SECONDS]:
            JOBS.pop(k, None)


def _job(job_id: str, request: Request) -> Job:
    _gc()
    j = JOBS.get(job_id)
    if not j or j.owner != _me(request).id:   # someone else's job looks the same as a missing one
        raise HTTPException(404, "ไม่พบงานนี้ (อาจหมดอายุแล้ว — อัปโหลดใหม่อีกครั้ง)")
    return j


# ---------------------------------------------------------------- sign-in gate
OPEN_PATHS = {"/", "/login", "/health", "/analyze", "/anonymize"}   # /analyze, /anonymize: machine callers (LiteLLM guardrail)
# The Presidio-compatible API stays open for LiteLLM on the same host; nginx should not expose it publicly.


def _path(request: Request) -> str:
    p, rp = request.scope.get("path", ""), request.scope.get("root_path", "")
    if rp and p.startswith(rp):
        p = p[len(rp):] or "/"
    return p


def _root(request: Request) -> str:
    return request.scope.get("root_path", "").rstrip("/")


def _me(request: Request) -> auth.User:
    return getattr(request.state, "user", None) or auth.LOCAL_ADMIN


def _who(request: Request) -> auth.User | None:
    if auth.mode() == "off":
        return auth.LOCAL_ADMIN
    return auth.session_user(request.cookies.get(auth.COOKIE))[0]


@app.middleware("http")
async def _gate(request: Request, call_next):
    path = _path(request)
    if path in OPEN_PATHS or path.startswith(("/static/", "/auth/")):
        return await call_next(request)
    user = _who(request)
    if user is None or not user.active:
        state = "login" if user is None else user.role
        if path.startswith("/api/"):
            msg = "กรุณาเข้าสู่ระบบ" if user is None else (
                "บัญชีนี้รอผู้ดูแลระบบอนุมัติ" if user.role == "pending" else "บัญชีนี้ถูกระงับการใช้งาน")
            return JSONResponse({"detail": msg, "auth": state}, status_code=401 if user is None else 403)
        return RedirectResponse(f"{_root(request)}/login" + ("" if user is None else f"?e={state}"), status_code=302)
    if path.startswith("/api/admin/") and not user.is_admin:
        return JSONResponse({"detail": "เฉพาะผู้ดูแลระบบ"}, status_code=403)
    request.state.user = user
    return await call_next(request)


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
    if "uid" not in cols:
        con.execute("alter table events add column uid int default 0")
    return con


def _record(**kw):
    try:
        with _db() as con:
            con.execute("insert into events(ts, kind, ext, seconds, findings, by_type, by_layer, models, action, mode, leaks,"
                        " extra, uid) values(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (time.time(), kw.get("kind"), kw.get("ext"), kw.get("seconds", 0), kw.get("findings", 0),
                         json.dumps(kw.get("by_type", {})), json.dumps(kw.get("by_layer", {})), json.dumps(kw.get("models", [])),
                         kw.get("action", ""), kw.get("mode", ""), kw.get("leaks", 0), json.dumps(kw.get("extra", {})),
                         int(kw.get("uid") or 0)))
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


# ---------------------------------------------------------------- sign-in
def _base(request: Request) -> str:
    """Absolute URL of the app root (no trailing slash), as the browser sees it."""
    return auth.public_url() or str(request.base_url).rstrip("/")


def _cookie_path(request: Request) -> str:
    return (_root(request) or "") + "/"


@app.get("/login", include_in_schema=False)
def login_page(request: Request):
    m = auth.mode()
    if m == "off":
        return RedirectResponse(f"{_root(request)}/app", status_code=302)
    user = auth.session_user(request.cookies.get(auth.COOKIE))[0]
    if user is not None and user.active and not request.query_params.get("e"):
        return RedirectResponse(f"{_root(request)}/app", status_code=302)
    return _page("login.html")


@app.get("/auth/status")
def auth_status(request: Request):
    """For the login page: which sign-in to offer and, if signed in, the account's state."""
    user = auth.session_user(request.cookies.get(auth.COOKIE))[0] if auth.mode() != "off" else None
    return {"mode": auth.mode(), "methods": auth.methods(), "user": user.public() if user else None}


@app.get("/auth/login", include_in_schema=False)
def auth_login(request: Request, next: str = "app"):
    if not auth.keycloak_enabled():
        return RedirectResponse(f"{_root(request)}/login", status_code=302)
    try:
        url = auth.login_url(f"{_base(request)}/auth/callback", next)
    except Exception:   # Keycloak unreachable: say so on the login page
        return RedirectResponse(f"{_root(request)}/login?e=idp", status_code=302)
    return RedirectResponse(url, status_code=302)


def _signed_in(request: Request, user: auth.User, id_token: str, nxt: str) -> Response:
    sid = auth.create_session(user.id, id_token)
    target = f"{_root(request)}/{auth.safe_next(nxt)}" if user.active else f"{_root(request)}/login?e={user.role}"
    resp = RedirectResponse(target, status_code=302)
    resp.set_cookie(auth.COOKIE, sid, max_age=auth.session_seconds(), httponly=True, samesite="lax",
                    secure=_base(request).startswith("https://"), path=_cookie_path(request))
    return resp


@app.get("/auth/callback", include_in_schema=False)
def auth_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    if error or not code or not state:
        return RedirectResponse(f"{_root(request)}/login?e={'denied' if error else 'expired'}", status_code=302)
    try:
        user, id_token, nxt = auth.finish_login(code, state, f"{_base(request)}/auth/callback")
    except auth.LoginError as e:
        return RedirectResponse(f"{_root(request)}/login?e={e}", status_code=302)
    except Exception:
        return RedirectResponse(f"{_root(request)}/login?e=idp", status_code=302)
    return _signed_in(request, user, id_token, nxt)


def _client_ip(request: Request) -> str:
    return (request.headers.get("x-forwarded-for", "").split(",")[0].strip()
            or (request.client.host if request.client else ""))


@app.post("/auth/password", include_in_schema=False)
async def auth_password(request: Request):
    """Username + password sign-in (form post from the login page)."""
    if not auth.password_enabled():
        raise HTTPException(404)
    form = await request.form()
    try:
        user = auth.password_login(str(form.get("username", ""))[:100], str(form.get("password", ""))[:200],
                                   _client_ip(request))
    except auth.LoginError as e:   # back to the form, keeping the page they were headed to (the #fragment)
        nxt = auth.safe_next(str(form.get("next", "app")))
        return RedirectResponse(f"{_root(request)}/login?e={e}{nxt[3:] if nxt.startswith('app#') else ''}", status_code=303)
    resp = _signed_in(request, user, "", str(form.get("next", "app")))
    resp.status_code = 303
    return resp


@app.get("/auth/logout", include_in_schema=False)
def auth_logout(request: Request):
    id_token = auth.end_session(request.cookies.get(auth.COOKIE))
    url = auth.logout_url(id_token, f"{_base(request)}/login") or f"{_root(request)}/login?bye=1"
    resp = RedirectResponse(url, status_code=302)
    resp.delete_cookie(auth.COOKIE, path=_cookie_path(request))
    return resp


@app.get("/api/me")
def api_me(request: Request):
    return {**_me(request).public(), "auth": auth.mode(), "methods": auth.methods()}


@app.post("/api/me/password")
def change_password(request: Request, body: dict = Body(...)):
    """Change your own password. Signs out your other sessions; this one gets a fresh cookie."""
    me = _me(request)
    if not me.id:
        raise HTTPException(400, "ระบบไม่ได้เปิดการเข้าสู่ระบบ")
    if not me.has_password:   # Microsoft-only account: the password lives with Microsoft (an admin can still set one)
        raise HTTPException(400, "บัญชีนี้เข้าสู่ระบบด้วย Microsoft — เปลี่ยนรหัสผ่านที่บัญชี Microsoft")
    try:
        auth.password_login(me.email, str(body.get("current", ""))[:200], _client_ip(request))
    except auth.LoginError as e:
        raise HTTPException(400, "รหัสผ่านปัจจุบันไม่ถูกต้อง" if str(e) == "bad_password" else
                            "ใส่รหัสผิดหลายครั้ง — รอ 5 นาทีแล้วลองใหม่")
    try:
        auth.set_password(me.id, str(body.get("new", "")))
    except ValueError as e:
        raise HTTPException(400, str(e))
    resp = JSONResponse({"ok": True})
    resp.set_cookie(auth.COOKIE, auth.create_session(me.id), max_age=auth.session_seconds(), httponly=True,
                    samesite="lax", secure=_base(request).startswith("https://"), path=_cookie_path(request))
    return resp


# ---------------------------------------------------------------- config / settings / llm
def _profiles_public() -> list[dict]:
    """Names and env var names only: base URLs and keys never leave the server."""
    return [{"name": p["name"], "model": p["model"], "env_url": f"LLM_{p['name'].upper()}_BASE_URL",
             "env_key": f"LLM_{p['name'].upper()}_API_KEY", "has_key": bool(p["api_key"])}
            for p in config.llm_profiles().values()]


@app.get("/api/config")
def api_config(request: Request):
    me = _me(request)
    return {
        "me": me.public(), "auth": auth.mode(), "methods": auth.methods(), "role_labels": auth.ROLE_LABELS,
        "entity_types": ENTITY_TYPES, "modes": MODES, "layers": LAYER_LABELS, "supported": SUPPORTED,
        "max_upload_mb": config.MAX_UPLOAD_MB, "review_score": config.REVIEW_SCORE,
        "min_score": config.MIN_SCORE, "auto_accept_score": config.AUTO_ACCEPT_SCORE,
        "llm": {"temperature": 0, "chunk_chars": config.LLM_CHUNK_CHARS, "concurrency": config.LLM_CONCURRENCY,
                "timeout": config.LLM_TIMEOUT, "verify_tls": config.LLM_VERIFY_TLS},
        "profiles": _profiles_public(), "default_profile": config.default_llm_profile(),
        "settings": _settings_out(me), "ttl_minutes": config.JOB_TTL_SECONDS // 60,
        "lists": {k: v[1] for k, v in gazetteer.LIST_FILES.items()},
        "packs": public_packs(), "law": law_map(), "presets": PRESETS,
        "active_types": _active_types(settings_for(me)),
    }


def _active_types(s: dict | None = None) -> list[str]:
    s = s or load_settings()
    return effective_types(s["packs_on"], s.get("types_off"))


def _personal(user: auth.User) -> bool:
    """Signed-in people keep their own settings and lists; with sign-in off there is only the shared set."""
    return bool(user.id)


def settings_for(user: auth.User) -> dict:
    """Shared settings with the person's own choices on top. Anything they never set follows the shared value,
    so an admin's change to the shared LLM reaches everyone who has not picked their own."""
    s = load_settings()
    if not _personal(user):
        return s
    own = auth.user_settings(user.id)
    if isinstance(own.get("models"), dict):
        s["models"] = {**s["models"], **own["models"]}
    s.update({k: v for k, v in own.items() if k != "models"})
    s["packs_on"] = clean_pack_ids(s.get("packs_on"))
    return s


def _settings_out(user: auth.User, scope: str = "own") -> dict:
    if scope == "central" or not _personal(user):
        s, own = load_settings(), []
    else:
        s, own = settings_for(user), sorted(auth.user_settings(user.id))
    return {**s, "active_types": _active_types(s), "_scope": "central" if scope == "central" or not _personal(user) else "own",
            "_own": own}


def _clean_patch(body: dict) -> dict:
    """The keys of a settings change that are valid, already cleaned. Unknown keys are dropped."""
    known = {p["name"] for p in _profiles_public()}
    out: dict = {}
    if isinstance(body.get("profiles"), list):
        out["profiles"] = [{"name": str(p.get("name", "")).lower(), "model": str(p.get("model", ""))[:200]}
                           for p in body["profiles"] if isinstance(p, dict) and str(p.get("name", "")).lower() in known][:6]
    if body.get("combine") in ("union", "vote"):
        out["combine"] = body["combine"]
    if "min_agree" in body:
        try:
            out["min_agree"] = max(2, min(6, int(body["min_agree"])))
        except (TypeError, ValueError):
            pass
    if body.get("mode") in MODES:
        out["mode"] = body["mode"]
    for k in ("sanitize", "use_llm"):
        if k in body:
            out[k] = bool(body[k])
    if isinstance(body.get("models"), dict):
        out["models"] = {str(k).lower(): str(v)[:200] for k, v in body["models"].items() if str(k).lower() in known}
    if "default_connector" in body:
        d = str(body["default_connector"]).lower()
        if d in known or d == "none":
            out["default_connector"] = d
    if isinstance(body.get("layers"), dict):
        out["layers"] = {k: bool(body["layers"].get(k, True)) for k in ("ner", "list")}
    if isinstance(body.get("type_modes"), dict):
        out["type_modes"] = {k: v for k, v in body["type_modes"].items() if k in ENTITY_TYPES and k != "SECRET" and v in MODES}
    if isinstance(body.get("types_off"), list):
        out["types_off"] = [t for t in body["types_off"] if t in ENTITY_TYPES and t != "SECRET"]
    if isinstance(body.get("packs_on"), list):
        out["packs_on"] = clean_pack_ids(body["packs_on"])
    if body.get("default_output") in ("ask", "redact", "highlight"):
        out["default_output"] = body["default_output"]
    return out


def _scope(request: Request, scope: str) -> str:
    """'central' (shared, admins only) or 'own'. With sign-in off everything is shared."""
    me = _me(request)
    if not _personal(me):
        return "central"
    if scope == "central":
        if not me.is_admin:
            raise HTTPException(403, "ค่ากลางแก้ได้เฉพาะผู้ดูแลระบบ")
        return "central"
    return "own"


@app.get("/api/settings")
def get_settings(request: Request, scope: str = "own"):
    me = _me(request)
    return _settings_out(me, _scope(request, scope) if scope == "central" else "own")


@app.put("/api/settings")
async def put_settings(request: Request, scope: str = "own"):
    me = _me(request)
    where = _scope(request, scope)
    patch = _clean_patch(await request.json())
    if where == "central":
        s = load_settings()
        if "models" in patch:
            s["models"].update(patch.pop("models"))
        s.update(patch)
        SETTINGS_FILE.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        own = auth.user_settings(me.id)
        if "models" in patch:
            own["models"] = {**(own.get("models") or {}), **patch.pop("models")}
        own.update(patch)
        auth.save_user_settings(me.id, own)
    return _settings_out(me, where)


@app.delete("/api/settings")
def reset_settings(request: Request, keys: str = ""):
    """Drop the person's own choices (all, or the comma-separated `keys`) so they follow the shared settings again."""
    me = _me(request)
    if _personal(me):
        own = auth.user_settings(me.id)
        for k in ([k for k in keys.split(",") if k] or list(own)):
            own.pop(k, None)
        auth.save_user_settings(me.id, own)
    return _settings_out(me)


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
def _count(content: str) -> int:
    return sum(1 for line in content.splitlines() if line.strip() and not line.lstrip().startswith("#"))


@app.get("/api/lists/{name}")
def get_list(name: str, request: Request, scope: str = "own"):
    """scope=own: the person's own list (a scan uses it on top of the shared one). scope=central: the shared list."""
    if name not in gazetteer.LIST_FILES:
        raise HTTPException(404)
    uid = None if _scope(request, scope) == "central" else _me(request).id
    content = gazetteer.read_list(name, uid)
    try:
        updated = gazetteer.list_path(name, uid).stat().st_mtime
    except OSError:
        updated = None
    return {"name": name, "content": content, "count": _count(content), "updated": updated,
            "scope": "central" if uid is None else "own", "central_count": _count(gazetteer.read_list(name))}


@app.put("/api/lists/{name}")
async def put_list(name: str, request: Request, scope: str = "own"):
    if name not in gazetteer.LIST_FILES:
        raise HTTPException(404)
    uid = None if _scope(request, scope) == "central" else _me(request).id
    body = await request.json()
    gazetteer.write_list(name, str(body.get("content", ""))[:500_000], uid)
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

    gazetteer.owner.set(job.owner or None)   # this worker thread scans with the owner's personal lists
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
                by_layer=dict(Counter(primary_layer(f) for f in res.findings)), models=res.llm_used, uid=job.owner)
    except Exception as e:  # keep the message generic: it may echo document text
        job.status, job.error = "error", f"ตรวจไม่สำเร็จ ({type(e).__name__})"


@app.post("/api/jobs")
async def create_job(request: Request, file: UploadFile | None = File(None), text: str = Form(""), options: str = Form("{}")):
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
    me = _me(request)
    s = settings_for(me)
    merged = {k: opts.get(k, s[k]) for k in ("profiles", "combine", "min_agree", "use_llm", "layers", "sanitize")}
    packs = clean_pack_ids(opts["packs"]) if isinstance(opts.get("packs"), list) else s["packs_on"]
    merged["entities"] = effective_types(packs, s.get("types_off"))
    merged["packs"] = packs
    job = Job(uuid.uuid4().hex, name, doc, options=merged, owner=me.id)
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
def get_job(job_id: str, request: Request):
    j = _job(job_id, request)
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
def delete_job(job_id: str, request: Request):
    with _lock:
        j = JOBS.get(job_id)
        if j and j.owner == _me(request).id:
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
    j = _job(job_id, request)
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
    j = _job(job_id, request)
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
            models=[m["model"] for m in j.result.models], uid=j.owner,
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
def pages(job_id: str, request: Request, body: dict = Body(...)):
    """Document as pages. what = source (with boxes for `items`) | redact | highlight (the file the export would make)."""
    j = _job(job_id, request)
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
def page_image(job_id: str, key: str, n: int, request: Request, w: int = 1100):
    j = _job(job_id, request)
    pdf = j.pages.get(key)
    if pdf is None:
        raise HTTPException(404)
    try:
        png = pageview.page_png(pdf, n, max(400, min(w, 2000)))
    except IndexError:
        raise HTTPException(404)
    return Response(png, media_type="image/png", headers={"Cache-Control": "private, max-age=3600"})


@app.get("/api/jobs/{job_id}/files/{name}")
def download(job_id: str, name: str, request: Request):
    j = _job(job_id, request)
    if name not in j.files:
        raise HTTPException(404)
    data, media = j.files[name]
    from urllib.parse import quote
    return Response(data, media_type=media, headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"})


# ---------------------------------------------------------------- analytics
_EVENT_COLS = "ts, kind, ext, seconds, findings, by_type, by_layer, models, action, mode, leaks, extra"


def _events(since: float, until: float, ext: str, model: str, uid: int | None = None) -> list[dict]:
    """uid None = everyone's events (admins); a number = that person's only."""
    q, args = f"select {_EVENT_COLS} from events where ts >= ? and ts < ?", [since, until]
    if uid is not None:
        q, args = q + " and uid = ?", args + [uid]
    with _db() as con:
        rows = con.execute(q + " order by ts", args).fetchall()
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
def stats(request: Request, days: int = 30, ext: str = "", model: str = "", mine: int = 0):
    """Admins see everyone's numbers (or only their own with mine=1); everyone else sees their own."""
    me = _me(request)
    uid = None if me.is_admin and not mine else me.id
    days = max(1, min(days, 365))
    now = time.time()
    since = now - days * 86400
    cur = _events(since, now + 1, ext, model, uid)
    prev = _events(since - days * 86400, since, ext, model, uid)
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

    with _db() as con:   # filter choices come from every event in view, not just the filtered range
        all_rows = con.execute("select ext, models from events" + ("" if uid is None else " where uid = ?"),
                               () if uid is None else (uid,)).fetchall()
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
        "scope": "all" if uid is None else "mine",
    }


@app.get("/api/leaderboard")
def leaderboard():
    """Offline evaluation results (eval/run_eval.py writes var/leaderboard.json). Empty until it has been run."""
    try:
        return json.loads((VAR / "leaderboard.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"rows": [], "columns": [], "dataset": None}


# ---------------------------------------------------------------- admin: users (the gate already checked the role)
def _admin_payload() -> dict:
    return {"users": [u.public() for u in auth.list_users()], "new_user_role": auth.new_user_role(),
            "auth": auth.mode(), "methods": auth.methods(), "min_password": auth.MIN_PASSWORD, "roles": [{"id": r, "label": auth.ROLE_LABELS[r]} for r in auth.ROLES]}


@app.get("/api/admin/users")
def admin_users():
    return _admin_payload()


@app.post("/api/admin/users")
def admin_add_user(request: Request, body: dict = Body(...)):
    try:
        auth.add_user(str(body.get("email", "")), str(body.get("role", "user")), str(body.get("name", "")),
                      added_by=_me(request).email, password=str(body.get("password", "")))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _admin_payload()


def _target(request: Request, uid: int) -> auth.User:
    u = auth.get_user(uid)
    if u is None:
        raise HTTPException(404, "ไม่พบผู้ใช้นี้")
    if u.id == _me(request).id:
        raise HTTPException(400, "เปลี่ยนบทบาทหรือลบบัญชีของตัวเองไม่ได้")
    if u.email in auth.admin_emails():
        raise HTTPException(400, "ผู้ดูแลระบบนี้กำหนดไว้ในการตั้งค่าเซิร์ฟเวอร์ แก้จากหน้านี้ไม่ได้")
    return u


@app.patch("/api/admin/users/{uid}")
def admin_set_role(uid: int, request: Request, body: dict = Body(...)):
    """{role} and/or {password} (reset: the person is signed out everywhere)."""
    _target(request, uid)
    try:
        if "role" in body:
            auth.set_role(uid, str(body.get("role", "")))
        if body.get("password"):
            auth.set_password(uid, str(body["password"]))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _admin_payload()


@app.delete("/api/admin/users/{uid}")
def admin_delete_user(uid: int, request: Request):
    _target(request, uid)
    auth.delete_user(uid)
    gazetteer.drop_user_lists(uid)
    with _lock:   # their open jobs go too
        for k in [k for k, j in JOBS.items() if j.owner == uid]:
            JOBS.pop(k, None)
    return _admin_payload()


@app.put("/api/admin/policy")
def admin_policy(body: dict = Body(...)):
    role = body.get("new_user_role")
    if role not in ("pending", "user"):
        raise HTTPException(400, "new_user_role ต้องเป็น pending หรือ user")
    auth.kv_set("new_user_role", role)
    return _admin_payload()


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
