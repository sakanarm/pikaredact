/* Sign-in pages inside the app: "ผู้ใช้งานและสิทธิ์" (admins: people, roles, passwords, the policy for
   first-time Microsoft sign-ins) and "บัญชีของฉัน" (profile, own password, own settings, sign out). */
(() => {
"use strict";
const PR = window.PR;

const blankAdd = () => ({ open: false, email: "", name: "", role: "user", password: "", busy: false, error: "" });
const A = { data: null, loading: false, error: "", q: "", add: blankAdd(), reset: null, del: null, busyRow: 0 };

const lastSeen = (ts) => (ts ? PR.ago(ts) : "ยังไม่เคยเข้าใช้");

async function load() {
  if (A.loading) return;
  A.loading = true;
  PR.render();
  try {
    A.data = await PR.api("api/admin/users");
    A.error = "";
  } catch (e) {
    A.error = e.message;
  }
  A.loading = false;
  PR.render();
}

/* every admin call returns the fresh list */
async function call(path, opts, ok, rowId) {
  A.busyRow = rowId || 0;
  PR.render();
  try {
    A.data = await PR.api(path, opts);
    if (ok) PR.toast(ok);
    return true;
  } catch (e) {
    PR.toast(e.message, true);
    return false;
  } finally {
    A.busyRow = 0;
    PR.render();
  }
}

async function addUser() {
  const f = A.add;
  if (f.busy) return;
  const login = f.email.trim().toLowerCase();
  const min = (A.data && A.data.min_password) || 8;
  if (!login) { f.error = "ใส่ชื่อผู้ใช้หรืออีเมล"; PR.render(); return; }
  if (f.password && f.password.length < min) { f.error = `รหัสผ่านอย่างน้อย ${min} ตัวอักษร`; PR.render(); return; }
  if (!f.password && !(A.data && A.data.methods.keycloak)) { f.error = "ยังไม่ได้เปิดเข้าด้วย Microsoft — ต้องตั้งรหัสผ่านให้ผู้ใช้นี้"; PR.render(); return; }
  f.busy = true;
  f.error = "";
  PR.render();
  try {
    A.data = await PR.api("api/admin/users", PR.json("POST", { email: login, name: f.name.trim(), role: f.role, password: f.password }));
    PR.toast(`เพิ่ม ${login} แล้ว`);
    A.add = blankAdd();
  } catch (e) {
    f.error = e.message;
    f.busy = false;
  }
  PR.render();
}

async function resetPassword() {
  const r = A.reset;
  if (!r || r.busy) return;
  const min = (A.data && A.data.min_password) || 8;
  if (r.pw.length < min) { r.error = `อย่างน้อย ${min} ตัวอักษร`; PR.render(); return; }
  r.busy = true;
  PR.render();
  const done = await call(`api/admin/users/${r.uid}`, PR.json("PATCH", { password: r.pw }), "ตั้งรหัสผ่านใหม่แล้ว · ผู้ใช้ถูกออกจากระบบทุกเครื่อง", r.uid);
  if (done) A.reset = null; else r.busy = false;
  PR.render();
}

function adminVals() {
  const d = A.data;
  const me = PR.me();
  const methods = (d && d.methods) || PR.cfg.methods || {};
  const roles = (d && d.roles) || [];
  const q = A.q.trim().toLowerCase();
  const all = (d && d.users) || [];
  const users = all.filter((u) => !q || `${u.email} ${u.name}`.toLowerCase().includes(q)).map((u) => {
    const self = u.id === me.id;
    const fixed = self || u.locked;
    const busy = A.busyRow === u.id;
    const resetting = !!A.reset && A.reset.uid === u.id;
    const deleting = A.del === u.id;
    return {
      id: u.id, init: PR.initials(u), name: u.name || u.email.split("@")[0], login: u.email,
      chip: PR.roleChip(u.role), self, locked: u.locked, fixed, editable: !fixed,
      pwTag: u.has_password, ssoTag: !u.has_password,
      seen: lastSeen(u.last_login), addedBy: u.added_by ? `เพิ่มโดย ${u.added_by}` : "",
      pending: u.role === "pending" && !fixed,
      busy, dis: busy ? "disabled" : "",
      opts: roles.map((r) => ({ v: r.id, label: r.label, sel: r.id === u.role ? "selected" : "" })),
      setRole: (ev) => { call(`api/admin/users/${u.id}`, PR.json("PATCH", { role: ev.target.value }), "เปลี่ยนบทบาทแล้ว", u.id); return false; },
      approve: () => { call(`api/admin/users/${u.id}`, PR.json("PATCH", { role: "user" }), `อนุมัติ ${u.email} แล้ว`, u.id); return false; },
      openReset: () => { A.reset = resetting ? null : { uid: u.id, pw: "", busy: false, error: "", show: false }; A.del = null; },
      openDel: () => { A.del = deleting ? null : u.id; A.reset = null; },
      resetting, deleting,
      resetId: `rp-${u.id}`,
      rpw: resetting ? A.reset.pw : "", rpwType: resetting && A.reset.show ? "text" : "password",
      rpwErr: resetting ? A.reset.error : "", rpwBusy: resetting && A.reset.busy ? "disabled" : "",
      onRpw: (ev) => { A.reset.pw = ev.target.value; A.reset.error = ""; return false; },
      rpwKey: (ev) => { if (ev.key === "Enter") { resetPassword(); return false; } return false; },
      toggleShow: () => { A.reset.show = !A.reset.show; },
      saveReset: () => { resetPassword(); return false; },
      confirmDel: () => { A.del = null; call(`api/admin/users/${u.id}`, { method: "DELETE" }, `ลบ ${u.email} แล้ว`, u.id); return false; },
      cancel: () => { A.del = null; A.reset = null; },
    };
  });
  const f = A.add;
  const policy = (d && d.new_user_role) || "pending";
  const pol = [["pending", "รออนุมัติก่อน", "แนะนำ · ผู้ดูแลระบบกดอนุมัติในหน้านี้"], ["user", "ใช้งานได้ทันที", "ทุกคนที่เข้าด้วย Microsoft ขององค์กรได้"]].map((o) => {
    const on = o[0] === policy;
    return { label: o[1], desc: o[2], on: String(on), border: on ? "#18181B" : "#E7E7E2", dot: on ? "#18181B" : "#FFFFFF", ringC: on ? "#18181B" : "#C4C4BE",
      pick: () => { if (!on) call("api/admin/policy", PR.json("PUT", { new_user_role: o[0] }), "บันทึกแล้ว"); return false; } };
  });
  const counts = {};
  for (const u of all) counts[u.role] = (counts[u.role] || 0) + 1;
  return {
    loading: A.loading && !d, error: A.error && !d ? A.error : "", ready: !!d,
    total: PR.num(all.length), pendingN: counts.pending || 0, pendingNote: counts.pending ? `${counts.pending} คนรออนุมัติ` : "",
    users, empty: !!d && !users.length, emptyText: q ? `ไม่พบ “${A.q.trim()}”` : "ยังไม่มีผู้ใช้",
    q: A.q, onQ: (ev) => { A.q = ev.target.value; },
    pwOn: !!methods.password, ssoOn: !!methods.keycloak,
    pwState: methods.password ? "เปิดใช้" : "ปิดอยู่", pwFg: methods.password ? "#166534" : "#71717A", pwDot: methods.password ? "#16A34A" : "#C4C4BE",
    ssoState: methods.keycloak ? "เชื่อมต่อแล้ว" : "ยังไม่ได้ตั้งค่า", ssoFg: methods.keycloak ? "#166534" : "#92400E", ssoDot: methods.keycloak ? "#16A34A" : "#D97706",
    pol, polNote: methods.keycloak ? "" : "มีผลเมื่อเชื่อม Keycloak แล้ว",
    addOpen: f.open, addClosed: !f.open,
    openAdd: () => { A.add = Object.assign(blankAdd(), { open: true }); setTimeout(() => { const el = document.getElementById("add-login"); if (el) el.focus(); }, 30); },
    closeAdd: () => { A.add = blankAdd(); },
    fEmail: f.email, fName: f.name, fPw: f.password, fErr: f.error, fBusy: f.busy ? "disabled" : "", fLabel: f.busy ? "กำลังเพิ่ม…" : "เพิ่มผู้ใช้",
    fRoles: roles.filter((r) => r.id !== "disabled").map((r) => ({ v: r.id, label: r.label, sel: r.id === f.role ? "selected" : "" })),
    onEmail: (ev) => { f.email = ev.target.value; f.error = ""; return false; },
    onName: (ev) => { f.name = ev.target.value; return false; },
    onPw: (ev) => { f.password = ev.target.value; f.error = ""; return false; },
    onRole: (ev) => { f.role = ev.target.value; return false; },
    onKey: (ev) => { if (ev.key === "Enter") addUser(); return false; },
    submit: () => { addUser(); return false; },
    pwHint: methods.keycloak ? `ไม่บังคับ · เว้นว่างถ้าจะเข้าด้วย Microsoft เท่านั้น (ใช้อีเมลเป็นชื่อผู้ใช้)` : `อย่างน้อย ${(d && d.min_password) || 8} ตัวอักษร · แจ้งผู้ใช้ให้เปลี่ยนเองที่หน้า “บัญชีของฉัน”`,
    minPw: (d && d.min_password) || 8,
  };
}

PR.views.admin = {
  tpl: "admin",
  title: () => "ผู้ใช้งานและสิทธิ์",
  enter() { A.reset = null; A.del = null; load(); },
  vals: adminVals,
};

/* ---------------------------------------------------------------- my account */
const P = { cur: "", nw: "", nw2: "", show: false, busy: false, error: "", resetting: false };

async function changePassword() {
  if (P.busy) return;
  const min = 8;
  if (!P.cur) { P.error = "ใส่รหัสผ่านปัจจุบัน"; PR.render(); return; }
  if (P.nw.length < min) { P.error = `รหัสผ่านใหม่อย่างน้อย ${min} ตัวอักษร`; PR.render(); return; }
  if (P.nw !== P.nw2) { P.error = "รหัสผ่านใหม่สองช่องไม่ตรงกัน"; PR.render(); return; }
  if (P.nw === P.cur) { P.error = "รหัสผ่านใหม่ต้องไม่ซ้ำกับรหัสเดิม"; PR.render(); return; }
  P.busy = true;
  P.error = "";
  PR.render();
  try {
    await PR.api("api/me/password", PR.json("POST", { current: P.cur, new: P.nw }));
    Object.assign(P, { cur: "", nw: "", nw2: "", show: false });
    PR.toast("เปลี่ยนรหัสผ่านแล้ว · เครื่องอื่นที่เข้าไว้จะถูกออกจากระบบ");
  } catch (e) {
    P.error = e.message;
  }
  P.busy = false;
  PR.render();
}

async function useCentral() {
  if (P.resetting) return;
  P.resetting = true;
  PR.render();
  try {
    const s = await PR.api("api/settings", { method: "DELETE" });
    if (s.active_types) PR.cfg.active_types = s.active_types;
    PR.cfg.settings = s;
    if (PR.resetUploadDefaults) PR.resetUploadDefaults();
    PR.toast("กลับไปใช้ค่ากลางทั้งหมดแล้ว");
  } catch (e) {
    PR.toast(e.message, true);
  }
  P.resetting = false;
  PR.render();
}

const OWN_LABELS = { default_connector: "AI ที่ใช้ตรวจ", models: "model", profiles: "ชุด AI", combine: "วิธีรวมผล AI", min_agree: "จำนวน AI ที่ต้องเห็นตรงกัน",
  mode: "โหมดปิดบัง", type_modes: "โหมดรายประเภท", types_off: "ประเภทที่ปิด", packs_on: "ชุดกฎ", default_output: "ผลลัพธ์เริ่มต้น",
  sanitize: "ล้าง metadata", use_llm: "ใช้ AI", layers: "ชั้นการตรวจ" };

function accountVals() {
  const me = PR.me();
  const methods = PR.cfg.methods || {};
  const own = ((PR.cfg.settings || {})._own) || [];
  const strength = !P.nw ? { text: "อย่างน้อย 8 ตัวอักษร", fg: "#71717A" } : P.nw.length < 8 ? { text: `อีก ${8 - P.nw.length} ตัวอักษร`, fg: "#B91C1C" }
    : ([/[a-z]/, /[A-Z]/, /\d/, /[^A-Za-z0-9]/].filter((r) => r.test(P.nw)).length >= 3 || P.nw.length >= 14) ? { text: "แข็งแรง", fg: "#166534" } : { text: "พอใช้ ลองเพิ่มตัวพิมพ์ใหญ่ ตัวเลข หรือสัญลักษณ์", fg: "#92400E" };
  return {
    init: PR.initials(me), name: me.name || me.email, login: me.email, chip: PR.roleChip(me.role),
    since: me.created ? `เริ่มใช้ ${PR.thDate(new Date(me.created * 1000))} ${new Date(me.created * 1000).getFullYear() + 543}` : "",
    via: me.has_password ? (methods.keycloak ? "ชื่อผู้ใช้และรหัสผ่าน หรือ Microsoft" : "ชื่อผู้ใช้และรหัสผ่าน") : "บัญชี Microsoft ขององค์กร",
    isAdmin: PR.isAdmin(),
    canPw: !!me.has_password, ssoOnly: !me.has_password,
    cur: P.cur, nw: P.nw, nw2: P.nw2, pwType: P.show ? "text" : "password", eyeLabel: P.show ? "ซ่อนรหัสผ่าน" : "แสดงรหัสผ่าน",
    toggle: () => { P.show = !P.show; },
    onCur: (ev) => { P.cur = ev.target.value; P.error = ""; return false; },
    onNw: (ev) => { P.nw = ev.target.value; P.error = ""; PR.render(); return false; },
    onNw2: (ev) => { P.nw2 = ev.target.value; P.error = ""; return false; },
    onKey: (ev) => { if (ev.key === "Enter") changePassword(); return false; },
    strength, err: P.error, busy: P.busy ? "disabled" : "", btn: P.busy ? "กำลังบันทึก…" : "เปลี่ยนรหัสผ่าน",
    save: () => { changePassword(); return false; },
    ownN: own.length, ownNone: !own.length, ownSome: !!own.length,
    ownList: own.map((k) => OWN_LABELS[k] || k).join(" · "),
    resetBusy: P.resetting ? "disabled" : "",
    useCentral: () => { useCentral(); return false; },
  };
}

PR.views.account = {
  tpl: "account",
  title: () => "บัญชีของฉัน",
  enter() { P.error = ""; },
  vals: accountVals,
};
})();
