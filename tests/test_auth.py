"""Sign-in: password and Keycloak, roles, the gate, per-user jobs / settings / lists, admin API."""
from __future__ import annotations

import base64
import json
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import auth  # noqa: E402
from app.detect import gazetteer  # noqa: E402

ADMIN_PW = "test-admin-pw-1"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "on")
    monkeypatch.setenv("ADMIN_USERNAME", "boss")
    monkeypatch.setenv("ADMIN_PASSWORD", ADMIN_PW)
    for k in ("KEYCLOAK_ISSUER", "KEYCLOAK_CLIENT_ID", "KEYCLOAK_CLIENT_SECRET", "PUBLIC_URL", "PIKA_ADMIN_EMAILS",
              "AUTH_PASSWORD_LOGIN"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(auth, "DB_PATH", tmp_path / "app.db")
    monkeypatch.setattr(auth, "_ready", False)
    monkeypatch.setattr(auth, "_fails", {})
    monkeypatch.setattr(gazetteer, "USER_LISTS_DIR", tmp_path / "user_lists")
    import app.main as main
    monkeypatch.setattr(main, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(main, "STATS_DB", tmp_path / "stats.db")
    return main


def client(main):
    from fastapi.testclient import TestClient
    return TestClient(main.app, follow_redirects=False)


def login(c, username, password):
    return c.post("/auth/password", data={"username": username, "password": password, "next": "app#/settings"})


def test_password_hash_roundtrip():
    h = auth.hash_password("correct horse")
    assert h.startswith("scrypt$") and "correct" not in h
    assert auth.check_password("correct horse", h) and not auth.check_password("wrong", h)
    assert not auth.check_password("x", "garbage")


def test_gate_and_password_login(env):
    c = client(env)
    assert c.get("/").status_code == 200 and c.get("/health").status_code == 200
    r = c.get("/app")
    assert r.status_code == 302 and r.headers["location"].endswith("/login")
    r = c.get("/api/config")
    assert r.status_code == 401 and r.json()["auth"] == "login"
    assert c.get("/login").status_code == 200

    r = login(c, "boss", "nope")
    assert r.status_code == 303 and r.headers["location"].endswith("/login?e=bad_password#/settings")
    r = login(c, "BOSS", ADMIN_PW)   # usernames are not case sensitive
    assert r.status_code == 303 and r.headers["location"].endswith("/app#/settings")
    assert "httponly" in r.headers["set-cookie"].lower()
    me = c.get("/api/me").json()
    assert me["email"] == "boss" and me["role"] == "admin" and me["has_password"] and "password" not in me
    assert c.get("/login").status_code == 302   # already in

    r = c.get("/auth/logout")
    assert r.status_code == 302 and "/login" in r.headers["location"]
    assert c.get("/api/me").status_code == 401


def test_lockout_after_failures(env):
    c = client(env)
    for _ in range(auth.MAX_FAILS):
        assert login(c, "boss", "bad").headers["location"].endswith("e=bad_password#/settings")
    assert login(c, "boss", ADMIN_PW).headers["location"].endswith("e=locked#/settings")


def test_unknown_user_and_password_off(env, monkeypatch):
    c = client(env)
    assert login(c, "ghost", "whatever1").headers["location"].endswith("e=bad_password#/settings")
    monkeypatch.setenv("AUTH_PASSWORD_LOGIN", "0")
    assert login(c, "boss", ADMIN_PW).status_code == 404


def test_roles_admin_api_and_isolation(env):
    a = client(env)
    login(a, "boss", ADMIN_PW)
    r = a.post("/api/admin/users", json={"email": "alice", "role": "user", "password": "alice-pw-1"})
    assert r.status_code == 200
    assert a.post("/api/admin/users", json={"email": "alice", "role": "user"}).status_code == 400
    assert a.post("/api/admin/users", json={"email": "bob", "role": "user", "password": "short"}).status_code == 400
    users = {u["email"]: u for u in r.json()["users"]}
    alice = users["alice"]
    boss = users["boss"]
    assert a.patch(f"/api/admin/users/{boss['id']}", json={"role": "user"}).status_code == 400   # not yourself

    u = client(env)
    assert login(u, "alice", "alice-pw-1").headers["location"].endswith("/app#/settings")
    assert u.get("/api/admin/users").status_code == 403
    assert u.put("/api/settings?scope=central", json={"mode": "mask"}).status_code == 403

    # jobs are private to their owner
    jid = u.post("/api/jobs", data={"text": "โทร 081-234-5678", "options": "{}"}).json()["id"]
    assert u.get(f"/api/jobs/{jid}").status_code == 200
    assert a.get(f"/api/jobs/{jid}").status_code == 404
    a.delete(f"/api/jobs/{jid}")
    assert u.get(f"/api/jobs/{jid}").status_code == 200

    # disabling takes effect at once on the open session
    a.patch(f"/api/admin/users/{alice['id']}", json={"role": "disabled"})
    assert u.get("/api/me").status_code == 403
    r = u.get("/app")
    assert r.status_code == 302 and r.headers["location"].endswith("/login?e=disabled")

    # admin password reset signs the person out
    a.patch(f"/api/admin/users/{alice['id']}", json={"role": "user", "password": "alice-pw-2"})
    assert u.get("/api/me").status_code == 401
    assert login(u, "alice", "alice-pw-2").headers["location"].endswith("/app#/settings")

    a.delete(f"/api/admin/users/{alice['id']}")
    assert u.get("/api/me").status_code == 401


def test_new_user_policy_and_pending(env):
    a = client(env)
    login(a, "boss", ADMIN_PW)
    assert a.get("/api/admin/users").json()["new_user_role"] == "pending"
    u = auth.signed_in("new.person@example.com", "New Person")   # first Keycloak sign-in
    assert u.role == "pending"
    assert a.put("/api/admin/policy", json={"new_user_role": "user"}).json()["new_user_role"] == "user"
    assert auth.signed_in("other@example.com", "").role == "user"
    assert a.put("/api/admin/policy", json={"new_user_role": "admin"}).status_code == 400


def test_settings_overlay_and_reset(env):
    a = client(env)
    login(a, "boss", ADMIN_PW)
    a.post("/api/admin/users", json={"email": "carol", "role": "user", "password": "carol-pw-1"})
    assert a.put("/api/settings?scope=central", json={"mode": "mask", "combine": "vote"}).json()["_scope"] == "central"

    u = client(env)
    login(u, "carol", "carol-pw-1")
    s = u.get("/api/settings").json()
    assert s["mode"] == "mask" and s["_own"] == []
    s = u.put("/api/settings", json={"mode": "fake", "bogus": 1}).json()
    assert s["mode"] == "fake" and s["combine"] == "vote" and s["_own"] == ["mode"]
    assert u.get("/api/config").json()["settings"]["mode"] == "fake"
    a.put("/api/settings?scope=central", json={"combine": "union"})
    assert u.get("/api/settings").json()["combine"] == "union"   # not set by carol: follows the shared value
    assert a.get("/api/settings").json()["mode"] == "mask"       # carol's choice is hers only
    s = u.delete("/api/settings").json()
    assert s["mode"] == "mask" and s["_own"] == []


def test_lists_personal_on_top_of_shared(env, monkeypatch, tmp_path):
    monkeypatch.setattr(gazetteer, "LISTS_DIR", tmp_path / "lists")
    a = client(env)
    login(a, "boss", ADMIN_PW)
    a.post("/api/admin/users", json={"email": "dan", "role": "user", "password": "dan-pw-12"})
    u = client(env)
    login(u, "dan", "dan-pw-12")
    assert u.put("/api/lists/persons?scope=central", json={"content": "x"}).status_code == 403
    assert a.put("/api/lists/persons?scope=central", json={"content": "สมศักดิ์ รักไทย"}).status_code == 200
    assert u.put("/api/lists/persons", json={"content": "วิไลวรรณ ทองดี"}).status_code == 200
    r = u.get("/api/lists/persons").json()
    assert r["scope"] == "own" and r["count"] == 1 and r["central_count"] == 1
    dan = auth.find_user("dan")
    try:
        gazetteer.owner.set(dan.id)
        texts = {norm for norm in _hits("ติดต่อ วิไลวรรณ ทองดี และ สมศักดิ์ รักไทย")}
        assert {"วิไลวรรณ ทองดี", "สมศักดิ์ รักไทย"} <= texts
        gazetteer.owner.set(None)
        assert "วิไลวรรณ ทองดี" not in set(_hits("ติดต่อ วิไลวรรณ ทองดี"))
    finally:
        gazetteer.owner.set(None)


def _hits(text):
    return [text[r.start:r.end] for r in gazetteer.GazetteerRecognizer().analyze(text, None)]


def test_failed_password_keeps_destination(env):
    c = client(env)
    r = c.post("/auth/password", data={"username": "boss", "password": "wrong", "next": "app#/settings/rules"})
    assert r.status_code == 303 and r.headers["location"].endswith("/login?e=bad_password#/settings/rules")
    r = c.post("/auth/password", data={"username": "boss", "password": "wrong", "next": "https://evil.example/#/x"})
    assert r.headers["location"].endswith("/login?e=bad_password")


def test_change_own_password(env):
    a = client(env)
    login(a, "boss", ADMIN_PW)
    a.post("/api/admin/users", json={"email": "erin", "role": "user", "password": "erin-pw-12"})
    u = client(env)
    login(u, "erin", "erin-pw-12")
    assert u.post("/api/me/password", json={"current": "wrong", "new": "erin-pw-34"}).status_code == 400
    assert u.post("/api/me/password", json={"current": "erin-pw-12", "new": "short"}).status_code == 400
    assert u.post("/api/me/password", json={"current": "erin-pw-12", "new": "erin-pw-34"}).status_code == 200
    assert u.get("/api/me").status_code == 200   # this session got a fresh cookie
    assert login(client(env), "erin", "erin-pw-34").headers["location"].endswith("/app#/settings")


def test_stats_are_per_user(env):
    a = client(env)
    login(a, "boss", ADMIN_PW)
    a.post("/api/admin/users", json={"email": "fay", "role": "user", "password": "fay-pw-123"})
    env._record(kind="scan", ext=".txt", findings=3, uid=auth.find_user("boss").id)
    env._record(kind="scan", ext=".txt", findings=5, uid=auth.find_user("fay").id)
    u = client(env)
    login(u, "fay", "fay-pw-123")
    assert u.get("/api/stats").json()["findings"] == 5
    assert a.get("/api/stats").json()["findings"] == 8
    assert a.get("/api/stats?mine=1").json()["findings"] == 3


def test_safe_next():
    assert auth.safe_next("app#/review/abc") == "app#/review/abc"
    for bad in ("https://evil.example", "//evil", "app/../x", "app#/x?y=<", ""):
        assert auth.safe_next(bad) == "app"


# ---------------------------------------------------------------- Keycloak (mocked)
def _jwt(claims: dict) -> str:
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()  # noqa: E731
    return f"{enc({'alg': 'RS256'})}.{enc(claims)}.sig"


class _Resp:
    def __init__(self, data):
        self._d = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._d


def test_keycloak_flow(env, monkeypatch):
    iss = "https://sso.example.test/realms/demo"
    monkeypatch.setenv("KEYCLOAK_ISSUER", iss + "/")
    monkeypatch.setenv("KEYCLOAK_CLIENT_ID", "pikaredact")
    monkeypatch.setenv("KEYCLOAK_CLIENT_SECRET", "s3cret")
    monkeypatch.setenv("PIKA_ADMIN_EMAILS", "Chief@Example.com")
    disc = {"issuer": iss, "authorization_endpoint": iss + "/auth", "token_endpoint": iss + "/token",
            "end_session_endpoint": iss + "/logout"}
    monkeypatch.setattr(auth, "discovery", lambda: disc)
    c = client(env)
    assert c.get("/auth/status").json()["methods"] == {"password": True, "keycloak": True}

    def start(email_claim):
        r = c.get("/auth/login?next=app%23/analytics")
        q = parse_qs(urlparse(r.headers["location"]).query)
        assert q["kc_idp_hint"] == ["entra"] and q["code_challenge_method"] == ["S256"]
        claims = {"iss": iss, "aud": "pikaredact", "exp": time.time() + 300, "nonce": q["nonce"][0], **email_claim}
        monkeypatch.setattr(auth.httpx, "post", lambda *a, **k: _Resp({"id_token": _jwt(claims)}))
        return c.get(f"/auth/callback?code=abc&state={q['state'][0]}")

    # an email the person typed themselves (unverified), or only a username, is not trusted
    assert start({"email": "chief@example.com"}).headers["location"].endswith("/login?e=unverified")
    assert start({"preferred_username": "chief@example.com"}).headers["location"].endswith("/login?e=no_email")
    assert start({"email": "chief@example.com", "email_verified": True, "azp": "other"}
                 ).headers["location"].endswith("/login?e=bad_token")
    r = start({"email": "chief@example.com", "email_verified": True, "name": "Chief"})
    assert r.headers["location"].endswith("/app#/analytics")
    assert c.get("/api/me").json()["role"] == "admin"   # from PIKA_ADMIN_EMAILS, case-insensitive
    # a Microsoft-only account has no password of its own to change here
    assert c.post("/api/me/password", json={"current": "", "new": "whatever-123"}).status_code == 400
    r = c.get("/auth/logout")
    loc = urlparse(r.headers["location"])
    assert loc.path.endswith("/logout") and "id_token_hint" in parse_qs(loc.query)

    r = start({"email": "Someone@Example.com", "email_verified": True})
    assert r.headers["location"].endswith("/login?e=pending")

    # KEYCLOAK_SHOW_BUTTON=0: no button on the login page, but the door still opens
    monkeypatch.setenv("KEYCLOAK_SHOW_BUTTON", "0")
    assert c.get("/auth/status").json()["methods"] == {"password": True, "keycloak": False}
    assert c.get("/auth/login").headers["location"].startswith(iss + "/auth?")
    monkeypatch.delenv("KEYCLOAK_SHOW_BUTTON")
    assert c.get("/api/config").status_code == 403

    # replayed / unknown state
    assert c.get("/auth/callback?code=abc&state=nope").headers["location"].endswith("/login?e=expired")

    # wrong audience
    r = c.get("/auth/login")
    q = parse_qs(urlparse(r.headers["location"]).query)
    bad = {"iss": iss, "aud": "someone-else", "exp": time.time() + 300, "nonce": q["nonce"][0], "email": "x@example.com"}
    monkeypatch.setattr(auth.httpx, "post", lambda *a, **k: _Resp({"id_token": _jwt(bad)}))
    assert c.get(f"/auth/callback?code=abc&state={q['state'][0]}").headers["location"].endswith("/login?e=bad_token")


def test_off_mode_is_open(env, monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "off")
    c = client(env)
    assert c.get("/api/me").json()["role"] == "admin"
    assert c.get("/login").status_code == 302
