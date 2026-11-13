"""Sign-in, users, roles and sessions.

Sign-in is on unless AUTH_MODE=off (then everyone is one local admin, as before sign-in existed).
Two ways in, both ending in the same users table and the same session cookie:
  password  username + password kept here (scrypt). The first admin comes from ADMIN_USERNAME /
            ADMIN_PASSWORD in .env, created once when missing; admins add the rest. AUTH_PASSWORD_LOGIN=0
            turns it off.
  keycloak  when KEYCLOAK_ISSUER + KEYCLOAK_CLIENT_ID + KEYCLOAK_CLIENT_SECRET are set: the organisation's
            Keycloak (OIDC authorization code + PKCE); kc_idp_hint sends people straight to Microsoft.
            Keycloak only proves who someone is: whether they may use the app is decided here.

Roles: admin (everything + users + shared settings), user (own work, own settings), pending (signed in,
waiting for an admin), disabled (blocked). The user row is read on every request, so a role change or a
block takes effect at once even though the Keycloak session lives on.

Sessions are server-side: the cookie holds a random id, the database keeps only its SHA-256.
The ID token comes straight from the token endpoint over TLS, so its claims are checked (iss, aud, exp,
nonce) without verifying the signature, as OIDC Core 3.1.3.7 allows for that case.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass
from urllib.parse import urlencode

import httpx

from . import config

DB_PATH = config.ROOT / "var" / "app.db"
ROLES = ("admin", "user", "pending", "disabled")
ROLE_LABELS = {"admin": "ผู้ดูแลระบบ", "user": "ผู้ใช้งาน", "pending": "รออนุมัติ", "disabled": "ระงับการใช้งาน"}
COOKIE = "pr_sid"
STATE_TTL = 600
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
LOGIN_RE = re.compile(r"^[a-z0-9][a-z0-9._@+\-]{1,99}$")   # a username or an email
MIN_PASSWORD = 8
MAX_FAILS, LOCK_SECONDS = 5, 300


# ---------------------------------------------------------------- settings from env
def issuer() -> str:
    return os.environ.get("KEYCLOAK_ISSUER", "").strip().rstrip("/")


def client_id() -> str:
    return os.environ.get("KEYCLOAK_CLIENT_ID", "").strip()


def client_secret() -> str:
    return os.environ.get("KEYCLOAK_CLIENT_SECRET", "").strip()


def mode() -> str:
    """'off' (no sign-in) or 'on'."""
    return "off" if os.environ.get("AUTH_MODE", "").strip().lower() == "off" else "on"


def keycloak_enabled() -> bool:
    return mode() == "on" and bool(issuer() and client_id() and client_secret())


def password_enabled() -> bool:
    return mode() == "on" and os.environ.get("AUTH_PASSWORD_LOGIN", "1").strip() != "0"


def keycloak_button() -> bool:
    """KEYCLOAK_SHOW_BUTTON=0 hides the button on the login page while <base>/auth/login still works,
    so Keycloak can be tried on prod before everyone sees it."""
    return keycloak_enabled() and os.environ.get("KEYCLOAK_SHOW_BUTTON", "1").strip().lower() not in ("0", "false", "no", "off")


def methods() -> dict:
    return {"password": password_enabled(), "keycloak": keycloak_button()}


def admin_emails() -> set[str]:
    """Bootstrap admins (PIKA_ADMIN_EMAILS, comma separated). Always admin; the UI cannot demote them."""
    return {e.strip().lower() for e in os.environ.get("PIKA_ADMIN_EMAILS", "").split(",") if e.strip()}


def session_seconds() -> int:
    try:
        return max(1, int(os.environ.get("SESSION_HOURS", "12"))) * 3600
    except ValueError:
        return 12 * 3600


def public_url() -> str:
    return os.environ.get("PUBLIC_URL", "").strip().rstrip("/")


def _verify_tls() -> bool:
    return os.environ.get("KEYCLOAK_VERIFY_TLS", "true").strip().lower() not in ("0", "false", "no")


# ---------------------------------------------------------------- database
_lock = threading.Lock()
_ready = False


def db() -> sqlite3.Connection:
    global _ready
    DB_PATH.parent.mkdir(exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    if not _ready:
        with _lock:
            con.executescript("""
            create table if not exists users(id integer primary key autoincrement, email text unique not null,
                name text default '', role text not null, created real, last_login real, added_by text default '');
            create table if not exists sessions(sid text primary key, uid int not null, created real, expires real,
                id_token text default '');
            create table if not exists login_states(state text primary key, nonce text, verifier text, next text,
                created real);
            create table if not exists user_settings(uid int primary key, data text not null);
            create table if not exists kv(k text primary key, v text);
            """)
            if "password" not in {r[1] for r in con.execute("pragma table_info(users)")}:
                con.execute("alter table users add column password text default ''")
            con.commit()
            _ready = True
            _bootstrap(con)
    return con


def _bootstrap(con: sqlite3.Connection) -> None:
    """First admin from ADMIN_USERNAME / ADMIN_PASSWORD, created once. Later password changes are kept."""
    name = os.environ.get("ADMIN_USERNAME", "").strip().lower()
    pw = os.environ.get("ADMIN_PASSWORD", "")
    if not name or not pw or not LOGIN_RE.match(name):
        return
    if con.execute("select 1 from users where email=?", (name,)).fetchone():
        return
    con.execute("insert into users(email, name, role, created, added_by, password) values(?,?,?,?,?,?)",
                (name, "Admin", "admin", time.time(), "env", hash_password(pw)))
    con.commit()


# ---------------------------------------------------------------- passwords
def hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(pw.encode("utf-8"), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return f"scrypt$16384$8$1${salt.hex()}${h.hex()}"


def check_password(pw: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt, h = stored.split("$")
        if algo != "scrypt":
            return False
        got = hashlib.scrypt(pw.encode("utf-8"), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p), dklen=len(h) // 2)
        return secrets.compare_digest(got.hex(), h)
    except (ValueError, TypeError):
        return False


_DUMMY = hash_password(secrets.token_hex(8))   # unknown usernames cost the same time as wrong passwords


def check_new_password(pw: str) -> None:
    if len(pw) < MIN_PASSWORD:
        raise ValueError(f"รหัสผ่านต้องยาวอย่างน้อย {MIN_PASSWORD} ตัวอักษร")
    if len(pw) > 200:
        raise ValueError("รหัสผ่านยาวเกินไป")


_fails: dict = {}   # (username, ip) -> (count, first failure time)


def password_login(username: str, password: str, ip: str = "") -> User:
    """Raises LoginError('locked' | 'bad_password')."""
    username = username.strip().lower()
    key = (username, ip)
    n, t0 = _fails.get(key, (0, 0.0))
    if n >= MAX_FAILS and time.time() - t0 < LOCK_SECONDS:
        raise LoginError("locked")
    with db() as con:
        r = con.execute("select id, password from users where email=?", (username,)).fetchone()
    stored = (r["password"] if r else "") or ""
    ok = check_password(password, stored) if stored else (check_password(password, _DUMMY) and False)
    if not ok:
        if time.time() - t0 >= LOCK_SECONDS:
            n, t0 = 0, time.time()
        _fails[key] = (n + 1, t0)
        if len(_fails) > 10_000:
            _fails.clear()
        raise LoginError("bad_password")
    _fails.pop(key, None)
    with db() as con:
        con.execute("update users set last_login=? where id=?", (time.time(), r["id"]))
    return get_user(r["id"])


def set_password(uid: int, pw: str) -> None:
    check_new_password(pw)
    with db() as con:
        con.execute("update users set password=? where id=?", (hash_password(pw), uid))
        con.execute("delete from sessions where uid=?", (uid,))   # sign out everywhere else


def has_password(uid: int) -> bool:
    with db() as con:
        r = con.execute("select password from users where id=?", (uid,)).fetchone()
    return bool(r and r["password"])


def kv_get(key: str, default: str = "") -> str:
    with db() as con:
        r = con.execute("select v from kv where k=?", (key,)).fetchone()
    return r["v"] if r else default


def kv_set(key: str, value: str) -> None:
    with db() as con:
        con.execute("insert into kv(k, v) values(?, ?) on conflict(k) do update set v=excluded.v", (key, value))


def new_user_role() -> str:
    """Role given to someone who signs in for the first time without being added by an admin."""
    r = kv_get("new_user_role", "pending")
    return r if r in ("pending", "user") else "pending"


# ---------------------------------------------------------------- users
@dataclass
class User:
    id: int
    email: str
    name: str
    role: str
    created: float = 0.0
    last_login: float | None = None
    added_by: str = ""
    has_password: bool = False

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    @property
    def active(self) -> bool:
        return self.role in ("admin", "user")

    def public(self) -> dict:
        d = asdict(self)
        d["role_label"] = ROLE_LABELS.get(self.role, self.role)
        d["locked"] = self.email in admin_emails()
        return d


LOCAL_ADMIN = User(0, "local", "ผู้ใช้ภายใน", "admin")


def _row(r) -> User | None:
    if not r:
        return None
    u = User(r["id"], r["email"], r["name"] or "", r["role"], r["created"] or 0, r["last_login"], r["added_by"] or "",
             bool(r["password"]))
    if u.email in admin_emails():
        u.role = "admin"
    return u


def get_user(uid: int) -> User | None:
    with db() as con:
        return _row(con.execute("select * from users where id=?", (uid,)).fetchone())


def find_user(email: str) -> User | None:
    with db() as con:
        return _row(con.execute("select * from users where email=?", (email.strip().lower(),)).fetchone())


def list_users() -> list[User]:
    with db() as con:
        return [_row(r) for r in con.execute("select * from users order by role='pending' desc, lower(name), email")]


def add_user(email: str, role: str, name: str = "", added_by: str = "", password: str = "") -> User:
    """email = the login: a username (password sign-in) or the email Keycloak reports. Without a password
    the person can only come in through Keycloak."""
    email = email.strip().lower()
    if not LOGIN_RE.match(email):
        raise ValueError("ชื่อผู้ใช้/อีเมลไม่ถูกต้อง (a-z 0-9 . _ - @ ยาว 2-100 ตัว)")
    if role not in ROLES:
        raise ValueError("บทบาทไม่ถูกต้อง")
    if password:
        check_new_password(password)
    with db() as con:
        try:
            con.execute("insert into users(email, name, role, created, added_by, password) values(?,?,?,?,?,?)",
                        (email, name.strip()[:120], role, time.time(), added_by, hash_password(password) if password else ""))
        except sqlite3.IntegrityError:
            raise ValueError("มีผู้ใช้ชื่อนี้อยู่แล้ว")
    return find_user(email)


def set_role(uid: int, role: str) -> User:
    if role not in ROLES:
        raise ValueError("บทบาทไม่ถูกต้อง")
    with db() as con:
        con.execute("update users set role=? where id=?", (role, uid))
    return get_user(uid)


def delete_user(uid: int) -> None:
    with db() as con:
        con.execute("delete from sessions where uid=?", (uid,))
        con.execute("delete from user_settings where uid=?", (uid,))
        con.execute("delete from users where id=?", (uid,))


def signed_in(email: str, name: str) -> User:
    """Called after Keycloak (or dev) has proven the email. Creates the row on first sign-in."""
    email = email.strip().lower()
    u = find_user(email)
    now = time.time()
    with db() as con:
        if u is None:
            role = "admin" if email in admin_emails() else new_user_role()
            con.execute("insert into users(email, name, role, created, last_login, added_by) values(?,?,?,?,?,?)",
                        (email, name[:120], role, now, now, "self"))
        else:
            con.execute("update users set last_login=?, name=case when ?<>'' then ? else name end where id=?",
                        (now, name[:120], name[:120], u.id))
    return find_user(email)


# ---------------------------------------------------------------- personal settings
def user_settings(uid: int) -> dict:
    with db() as con:
        r = con.execute("select data from user_settings where uid=?", (uid,)).fetchone()
    try:
        return json.loads(r["data"]) if r else {}
    except ValueError:
        return {}


def save_user_settings(uid: int, data: dict) -> None:
    with db() as con:
        if data:
            con.execute("insert into user_settings(uid, data) values(?, ?) on conflict(uid) do update set data=excluded.data",
                        (uid, json.dumps(data, ensure_ascii=False)))
        else:
            con.execute("delete from user_settings where uid=?", (uid,))


# ---------------------------------------------------------------- sessions
def _hash(sid: str) -> str:
    return hashlib.sha256(sid.encode()).hexdigest()


def create_session(uid: int, id_token: str = "") -> str:
    sid = secrets.token_urlsafe(32)
    now = time.time()
    with db() as con:
        con.execute("delete from sessions where expires < ?", (now,))
        con.execute("insert into sessions values(?,?,?,?,?)", (_hash(sid), uid, now, now + session_seconds(), id_token))
    return sid


def session_user(sid: str | None) -> tuple[User | None, str]:
    """(user, id_token) for a live session cookie, else (None, "")."""
    if not sid or len(sid) > 100:
        return None, ""
    with db() as con:
        r = con.execute("select uid, expires, id_token from sessions where sid=?", (_hash(sid),)).fetchone()
    if not r or r["expires"] < time.time():
        return None, ""
    return get_user(r["uid"]), r["id_token"] or ""


def end_session(sid: str | None) -> str:
    """Deletes the session; returns its id_token (for the Keycloak logout hint)."""
    if not sid:
        return ""
    with db() as con:
        r = con.execute("select id_token from sessions where sid=?", (_hash(sid),)).fetchone()
        con.execute("delete from sessions where sid=?", (_hash(sid),))
    return (r["id_token"] or "") if r else ""


# ---------------------------------------------------------------- OIDC
_disc: dict = {"at": 0.0, "doc": None, "iss": ""}


def discovery() -> dict:
    if _disc["doc"] and _disc["iss"] == issuer() and time.time() - _disc["at"] < 3600:
        return _disc["doc"]
    r = httpx.get(f"{issuer()}/.well-known/openid-configuration", timeout=10, verify=_verify_tls())
    r.raise_for_status()
    _disc.update(at=time.time(), doc=r.json(), iss=issuer())
    return _disc["doc"]


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def safe_next(nxt: str | None) -> str:
    """Only places inside the app: 'app' plus an optional #/route. Anything else becomes 'app'."""
    nxt = (nxt or "").strip()
    if re.fullmatch(r"app(#/[A-Za-z0-9/_\-]*)?", nxt):
        return nxt
    return "app"


def login_url(redirect_uri: str, nxt: str) -> str:
    d = discovery()
    state, nonce, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(24), secrets.token_urlsafe(48)
    now = time.time()
    with db() as con:
        con.execute("delete from login_states where created < ?", (now - STATE_TTL,))
        con.execute("insert into login_states values(?,?,?,?,?)", (state, nonce, verifier, safe_next(nxt), now))
    params = {
        "response_type": "code", "client_id": client_id(), "redirect_uri": redirect_uri,
        "scope": "openid email profile", "state": state, "nonce": nonce,
        "code_challenge": _b64url(hashlib.sha256(verifier.encode()).digest()), "code_challenge_method": "S256",
    }
    hint = os.environ.get("KEYCLOAK_IDP_HINT", "entra").strip()
    if hint:
        params["kc_idp_hint"] = hint
    return f"{d['authorization_endpoint']}?{urlencode(params)}"


class LoginError(Exception):
    pass


def _claims(id_token: str) -> dict:
    try:
        payload = id_token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (IndexError, ValueError):
        raise LoginError("bad_token")


def finish_login(code: str, state: str, redirect_uri: str) -> tuple[User, str, str]:
    """Exchanges the code. Returns (user, id_token, next)."""
    with db() as con:
        row = con.execute("select * from login_states where state=?", (state,)).fetchone()
        con.execute("delete from login_states where state=?", (state,))
    if not row or time.time() - row["created"] > STATE_TTL:
        raise LoginError("expired")
    d = discovery()
    try:
        r = httpx.post(d["token_endpoint"], data={
            "grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri,
            "client_id": client_id(), "client_secret": client_secret(), "code_verifier": row["verifier"],
        }, timeout=15, verify=_verify_tls())
        r.raise_for_status()
        id_token = r.json().get("id_token") or ""
    except (httpx.HTTPError, ValueError):
        raise LoginError("token")
    c = _claims(id_token)
    aud = c.get("aud")
    auds = aud if isinstance(aud, list) else [aud]
    if (c.get("iss") or "").rstrip("/") != (d.get("issuer") or issuer()).rstrip("/") or client_id() not in auds:
        raise LoginError("bad_token")
    # azp must be us when present, and present when there are several audiences (OIDC Core 3.1.3.7)
    if c.get("azp") not in (None, client_id()) or (len(auds) > 1 and c.get("azp") is None):
        raise LoginError("bad_token")
    if float(c.get("exp") or 0) < time.time() - 60 or c.get("nonce") != row["nonce"]:
        raise LoginError("bad_token")
    # Only an email Keycloak has verified. People can edit their own email (and username) on the
    # Keycloak account page, so trusting an unverified one would let anyone claim an admin's address.
    email = (c.get("email") or "").strip().lower()
    if not EMAIL_RE.match(email):
        raise LoginError("no_email")
    if c.get("email_verified") is not True:
        raise LoginError("unverified")
    name = c.get("name") or " ".join(x for x in (c.get("given_name"), c.get("family_name")) if x) or ""
    return signed_in(email, str(name)), id_token, row["next"]


def logout_url(id_token: str, post_logout: str) -> str | None:
    """Keycloak sign-out, for sessions that came through Keycloak (they carry an id_token)."""
    if not keycloak_enabled() or not id_token:
        return None
    try:
        end = discovery().get("end_session_endpoint") or f"{issuer()}/protocol/openid-connect/logout"
    except (httpx.HTTPError, ValueError):
        end = f"{issuer()}/protocol/openid-connect/logout"
    params = {"client_id": client_id(), "post_logout_redirect_uri": post_logout}
    if id_token:
        params["id_token_hint"] = id_token
    return f"{end}?{urlencode(params)}"
