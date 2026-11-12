/* PikaRedact app runtime: tiny template renderer, hash router, API + crypto helpers.
   Templates live in static/tpl/*.html and use {{expr}}, {{{raw}}}, <sc-for>, <sc-if>,
   <sc-include> and onEvent="{{fn}}" bindings. Each view supplies vals() for its template. */
(() => {
"use strict";
const PR = window.PR = { tpl: {}, views: {}, review: {}, results: {}, cfg: null, route: null };

/* ---------------------------------------------------------------- templates */
const TOKEN = /<sc-for\s+list="\{\{([\s\S]+?)\}\}"\s+as="(\w+)"\s*>|<\/sc-for>|<\/sc-if>|<sc-if\s+value="\{\{([\s\S]+?)\}\}"\s*>|\son([A-Z]\w*)="\{\{([\s\S]+?)\}\}"|\{\{\{([\s\S]+?)\}\}\}|\{\{([\s\S]+?)\}\}/g;
const compiled = {};

function inline(src, depth = 0) {
  return src.replace(/<sc-include\s+name="([\w-]+)"\s*\/?>(?:\s*<\/sc-include>)?/g,
    (_, n) => depth > 4 ? "" : inline(PR.tpl[n] || "", depth + 1));
}
function compile(name) {
  if (compiled[name]) return compiled[name];
  const src = inline(PR.tpl[name] || "");
  let code = "let __o='';with(__p){", last = 0, m;
  TOKEN.lastIndex = 0;
  while ((m = TOKEN.exec(src))) {
    code += "__o+=" + JSON.stringify(src.slice(last, m.index)) + ";";
    last = TOKEN.lastIndex;
    if (m[1] !== undefined) code += `for(const ${m[2]} of ((${m[1]})||[])){`;
    else if (m[0] === "</sc-for>" || m[0] === "</sc-if>") code += "}";
    else if (m[3] !== undefined) code += `if(${m[3]}){`;
    else if (m[4] !== undefined) code += `__o+=' data-ev-${m[4].toLowerCase()}="'+__H(${m[5]})+'"';`;
    else if (m[6] !== undefined) code += `__o+=((${m[6]})??'');`;
    else code += `__o+=__E(${m[7]});`;
  }
  code += "__o+=" + JSON.stringify(src.slice(last)) + ";}return __o;";
  return (compiled[name] = new Function("__p", "__E", "__H", code));
}
const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
const esc = (v) => (v == null ? "" : String(v).replace(/[&<>"']/g, (c) => ESC[c]));
const scope = (t) => new Proxy(t, {
  has: (o, k) => typeof k === "string" && !k.startsWith("__") && (k in o || !(k in globalThis)),
  get: (o, k) => (k === Symbol.unscopables ? undefined : o[k]),
});

const FOCUSABLE = "input,textarea,select,button,a[href],[tabindex]";
const EVENTS = ["click", "input", "change", "keydown", "dragover", "dragleave", "drop", "mouseup"];

function paint(root, name, vals, slots) {
  const handlers = [];
  const html = compile(name)(scope(vals), esc, (fn) => (typeof fn === "function" ? handlers.push(fn) - 1 : -1));
  root.__h = handlers;
  if (html === root.__html) return false;
  // remember focus, caret and scroll positions so a re-render feels like an in-place update
  const act = document.activeElement;
  let focus = null;
  if (act && act !== document.body && root.contains(act)) {
    focus = { id: act.id, idx: [...root.querySelectorAll(FOCUSABLE)].indexOf(act) };
    try { focus.s = act.selectionStart; focus.e = act.selectionEnd; } catch (_) { /* not a text field */ }
  }
  const scroll = {};
  root.querySelectorAll("[data-keep-scroll]").forEach((el) => { scroll[el.dataset.keepScroll] = el.scrollTop; });
  root.innerHTML = html;
  root.__html = html;
  // put persistent nodes back before anything measures layout, or the page height (and scroll) would jump
  if (slots) root.querySelectorAll("[data-slot]").forEach((el) => {
    const make = slots[el.dataset.slot];
    const node = make && make();
    if (node) el.appendChild(node);
  });
  root.querySelectorAll("[data-keep-scroll]").forEach((el) => {
    if (scroll[el.dataset.keepScroll] != null) el.scrollTop = scroll[el.dataset.keepScroll];
  });
  if (focus) {
    const el = (focus.id && document.getElementById(focus.id)) || root.querySelectorAll(FOCUSABLE)[focus.idx];
    if (el) {
      el.focus({ preventScroll: true });
      if (focus.s != null && el.setSelectionRange) try { el.setSelectionRange(focus.s, focus.e); } catch (_) { /* ignore */ }
    }
  }
  return true;
}

function bind(root) {
  for (const type of EVENTS) {
    root.addEventListener(type, (ev) => {
      const el = ev.target.closest && ev.target.closest(`[data-ev-${type}]`);
      if (!el || !root.contains(el)) return;
      const fn = root.__h && root.__h[+el.getAttribute(`data-ev-${type}`)];
      if (!fn) return;
      const r = fn(ev, el);
      if (r !== false && !ev.isComposing) PR.render();
    });
  }
}

let queued = false;
PR.render = () => {
  if (queued) return;
  queued = true;
  requestAnimationFrame(() => { queued = false; renderNow(); });
};
function renderNow() {
  const side = document.getElementById("side"), view = document.getElementById("view");
  try {
    paint(side, "shell", shellVals());
    const v = PR.current;
    if (v) {
      paint(view, v.tpl, v.vals(), v.slots);
      if (v.after) v.after(view);
    }
  } catch (e) {
    console.error(e);
  }
}
PR.renderNow = renderNow;
PR.paint = paint;
PR.bind = bind;

/* ---------------------------------------------------------------- api */
PR.api = async (path, opts = {}) => {
  let r;
  try {
    r = await fetch(path, opts);
  } catch (_) {
    throw new Error("เชื่อมต่อเซิร์ฟเวอร์ไม่ได้ ลองใหม่อีกครั้ง");
  }
  const ct = r.headers.get("content-type") || "";
  const body = ct.includes("json") ? await r.json().catch(() => null) : await r.text();
  if (!r.ok) {
    let msg = `เกิดข้อผิดพลาด (HTTP ${r.status})`;
    if (body && body.detail) msg = typeof body.detail === "string" ? body.detail : (body.detail[0] && body.detail[0].msg) || msg;
    if (r.status === 404 && path.includes("api/jobs/")) msg = "ไม่พบงานนี้แล้ว ไฟล์อาจหมดเวลาและถูกลบ";
    const err = new Error(msg);
    err.status = r.status;
    throw err;
  }
  return body;
};
PR.json = (method, data) => ({ method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });

PR.store = {
  get(k, d) { try { const v = localStorage.getItem(k); return v == null ? d : JSON.parse(v); } catch (_) { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (_) { /* private mode */ } },
};

/* job history: names and counts only, never the values that were found */
PR.hist = {
  all() { return PR.store.get("pr.history", []); },
  get(id) { return PR.hist.all().find((h) => h.id === id); },
  put(id, patch) {
    const a = PR.hist.all();
    const i = a.findIndex((h) => h.id === id);
    if (i >= 0) Object.assign(a[i], patch);
    else a.unshift(Object.assign({ id }, patch));
    PR.store.set("pr.history", a.slice(0, 30));
  },
  state(h) {
    if (!h.expires || h.expires <= Date.now() / 1000) return "gone";
    return h.downloaded || h.exported ? "ok" : "wait";
  },
};

/* ---------------------------------------------------------------- formatting */
const EXT_COLORS = { DOCX: "#2563EB", XLSX: "#15803D", PPTX: "#C2410C", PDF: "#B91C1C", CSV: "#15803D" };
PR.extColor = (ext) => EXT_COLORS[String(ext || "").replace(".", "").toUpperCase()] || "#52525B";
PR.extLabel = (ext) => String(ext || "").replace(".", "").toUpperCase().slice(0, 4) || "TXT";
const KIND = { docx: "เอกสาร Word", xlsx: "Excel", pptx: "PowerPoint", pdf: "PDF", text: "ข้อความ", csv: "CSV" };
PR.kindLabel = (k) => KIND[k] || k || "ไฟล์";
PR.num = (n) => (n == null ? "–" : Number(n).toLocaleString("en-US"));
PR.size = (b) => (b < 1024 ? `${b} B` : b < 1048576 ? `${(b / 1024).toFixed(b < 10240 ? 1 : 0)} KB` : `${(b / 1048576).toFixed(1)} MB`);
PR.pct = (v, d = 1) => (v == null ? "–" : `${(v * 100).toFixed(d)}%`);
PR.ago = (ts) => {
  const s = Date.now() / 1000 - ts;
  if (s < 60) return "เมื่อสักครู่";
  if (s < 3600) return `${Math.floor(s / 60)} นาทีที่แล้ว`;
  if (s < 86400) return `${Math.floor(s / 3600)} ชั่วโมงที่แล้ว`;
  if (s < 172800) return "เมื่อวาน";
  return `${Math.floor(s / 86400)} วันที่แล้ว`;
};
const TH_MONTHS = ["ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.", "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."];
PR.thDate = (d) => `${d.getDate()} ${TH_MONTHS[d.getMonth()]}`;
PR.esc = esc;

/* entity type colours (label comes from the server's config) */
const TYPE_COLORS = {
  PERSON: ["#FDE7F1", "#DB2777", "#9D174D"], ORGANIZATION: ["#DBEAFE", "#2563EB", "#1E40AF"],
  PHONE: ["#E0F2FE", "#0369A1", "#075985"], EMAIL: ["#CFFAFE", "#0E7490", "#155E75"],
  TH_ID_CARD: ["#FFEDD5", "#C2410C", "#9A3412"], BANK_ACCOUNT: ["#ECFCCB", "#4D7C0F", "#3F6212"],
  IP_ADDRESS: ["#E2E8F0", "#475569", "#334155"], SECRET: ["#FEE2E2", "#B91C1C", "#991B1B"],
  OTHER_ID: ["#F1F5F9", "#64748B", "#334155"], ADDRESS: ["#FEF3C7", "#B45309", "#92400E"],
  CREDIT_CARD: ["#FEF9C3", "#A16207", "#854D0E"], PASSPORT: ["#DCFCE7", "#15803D", "#166534"],
  DATE_OF_BIRTH: ["#F3E8FF", "#9333EA", "#6B21A8"], TH_LICENSE_PLATE: ["#EDE9FE", "#7C3AED", "#5B21B6"],
};
PR.type = (t) => {
  const et = (PR.cfg && PR.cfg.entity_types[t]) || {};
  // newer types: tint the server colour (8-digit hex = colour + alpha)
  const c = TYPE_COLORS[t] || (et.color ? [et.color + "1F", et.color, et.color] : ["#F1F5F9", "#64748B", "#334155"]);
  const law = ((PR.cfg && PR.cfg.law) || {})[t] || [];
  return { label: et.label || t, bg: c[0], line: c[1], fg: c[2], sensitive: !!et.sensitive, law: law.join(" · ") };
};
/* types the current settings will look for (packs on, minus per-type switches) */
PR.activeTypes = () => new Set((PR.cfg && PR.cfg.active_types) || Object.keys((PR.cfg && PR.cfg.entity_types) || {}));
PR.modeLabel = (m) => ({ remove: "ลบทิ้ง", mask: "ปิดบางส่วน", token: "แทนด้วยรหัส", fake: "ข้อมูลปลอม" }[m] || m);

/* ---------------------------------------------------------------- toast */
let toastEl = null, toastTimer = 0;
PR.toast = (msg, bad) => {
  if (!toastEl) {
    toastEl = document.createElement("div");
    toastEl.className = "toast";
    toastEl.setAttribute("role", "status");
    document.body.appendChild(toastEl);
  }
  toastEl.textContent = msg;
  toastEl.classList.toggle("bad", !!bad);
  toastEl.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toastEl.classList.remove("show"), bad ? 6000 : 3200);
};

/* ---------------------------------------------------------------- files + crypto */
PR.save = (blob, name) => {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 4000);
};
const b64 = (buf) => {
  const u8 = new Uint8Array(buf);
  let s = "";
  for (let i = 0; i < u8.length; i += 0x8000) s += String.fromCharCode.apply(null, u8.subarray(i, i + 0x8000));
  return btoa(s);
};
const unb64 = (s) => Uint8Array.from(atob(s), (c) => c.charCodeAt(0));
const NO_CRYPTO = "เบราว์เซอร์ไม่อนุญาตการเข้ารหัสบนหน้าที่ไม่ใช่ HTTPS — เปิดผ่าน https:// แล้วลองใหม่";
async function deriveKey(pass, salt, iter) {
  const base = await crypto.subtle.importKey("raw", new TextEncoder().encode(pass), "PBKDF2", false, ["deriveKey"]);
  return crypto.subtle.deriveKey({ name: "PBKDF2", salt, iterations: iter, hash: "SHA-256" }, base,
    { name: "AES-GCM", length: 256 }, false, ["encrypt", "decrypt"]);
}
PR.MIN_PASS = 12;
PR.encryptMap = async (mapping, pass) => {
  if (!(window.crypto && crypto.subtle)) throw new Error(NO_CRYPTO);
  const salt = crypto.getRandomValues(new Uint8Array(16)), iv = crypto.getRandomValues(new Uint8Array(12)), iter = 310000;
  const key = await deriveKey(pass, salt, iter);
  const data = await crypto.subtle.encrypt({ name: "AES-GCM", iv }, key, new TextEncoder().encode(JSON.stringify({ mapping })));
  return { format: "piimap", v: 1, kdf: "PBKDF2-SHA256", iter, cipher: "AES-256-GCM", salt: b64(salt), iv: b64(iv),
    data: b64(data), created: new Date().toISOString(), count: Object.keys(mapping).length };
};
PR.decryptMap = async (fileText, pass) => {
  if (!(window.crypto && crypto.subtle)) throw new Error(NO_CRYPTO);
  let p;
  try { p = JSON.parse(fileText); } catch (_) { throw new Error("ไฟล์นี้ไม่ใช่ไฟล์กุญแจ .piimap"); }
  if (!p || p.format !== "piimap") throw new Error("ไฟล์นี้ไม่ใช่ไฟล์กุญแจ .piimap");
  try {
    const key = await deriveKey(pass, unb64(p.salt), p.iter);
    const plain = await crypto.subtle.decrypt({ name: "AES-GCM", iv: unb64(p.iv) }, key, unb64(p.data));
    return JSON.parse(new TextDecoder().decode(plain)).mapping || {};
  } catch (_) {
    throw new Error("เปิดไฟล์กุญแจไม่ได้ รหัสผ่านไม่ถูกต้องหรือไฟล์เสียหาย");
  }
};
PR.passHint = (pw) => {
  if (!pw) return { text: `อย่างน้อย ${PR.MIN_PASS} ตัวอักษร`, fg: "#71717A", ok: false };
  if (pw.length < PR.MIN_PASS) return { text: `อีก ${PR.MIN_PASS - pw.length} ตัวอักษร`, fg: "#B91C1C", ok: false };
  const kinds = [/[a-z]/, /[A-Z]/, /\d/, /[^A-Za-z0-9]/].filter((r) => r.test(pw)).length;
  return kinds >= 3 || pw.length >= 16 ? { text: "แข็งแรง", fg: "#006300", ok: true } : { text: "พอใช้ ลองเพิ่มตัวเลขหรือสัญลักษณ์", fg: "#92400E", ok: true };
};
/* token text -> parts, so restored values can be marked */
PR.restoreParts = (text, mapping) => {
  const keys = Object.keys(mapping || {}).sort((a, b) => b.length - a.length);
  const known = keys.map((k) => k.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  const re = new RegExp((known.length ? known.join("|") + "|" : "") + "\\[[A-Z_]+_\\d+\\]", "g");
  const parts = [];
  let last = 0, m, hit = 0, miss = 0;
  while ((m = re.exec(text))) {
    if (m.index > last) parts.push({ t: text.slice(last, m.index) });
    if (Object.prototype.hasOwnProperty.call(mapping, m[0])) { parts.push({ t: mapping[m[0]], restored: true }); hit++; }
    else { parts.push({ t: m[0], unknown: true }); miss++; }
    last = re.lastIndex;
  }
  if (last < text.length) parts.push({ t: text.slice(last) });
  return { parts, hit, miss, text: parts.map((p) => p.t).join("") };
};

/* ---------------------------------------------------------------- shell */
PR.ttlMinutes = () => (PR.cfg ? PR.cfg.ttl_minutes : 60);
PR.connector = () => {
  const cfg = PR.cfg;
  if (!cfg) return null;
  const st = cfg.settings || {};
  const name = st.default_connector && st.default_connector !== "none" ? st.default_connector : (st.profiles || [])[0];
  const p = (cfg.profiles || []).find((x) => x.name === name);
  if (!p || st.default_connector === "none") return null;
  return { name: p.name, model: (st.models || {})[p.name] || p.model || "", ready: p.has_key && !!((st.models || {})[p.name] || p.model) };
};
function shellVals() {
  const r = PR.route || { name: "" };
  const on = (cond) => (cond ? "on" : "");
  const cur = (cond) => (cond ? "page" : "false");
  const tab = r.params && r.params[0];
  const isUp = ["upload", "review", "result"].includes(r.name);
  const waiting = PR.hist.all().filter((h) => PR.hist.state(h) === "wait").length;
  const c = PR.connector();
  return {
    upOn: on(isUp), upCur: cur(isUp),
    restoreOn: on(r.name === "restore"), restoreCur: cur(r.name === "restore"),
    anOn: on(r.name === "analytics" && tab === "analytics"), anCur: cur(r.name === "analytics" && tab === "analytics"),
    lbOn: on(r.name === "analytics" && tab === "leaderboard"), lbCur: cur(r.name === "analytics" && tab === "leaderboard"),
    connOn: on(r.name === "settings" && tab === "conn"), connCur: cur(r.name === "settings" && tab === "conn"),
    listsOn: on(r.name === "settings" && tab === "lists"), listsCur: cur(r.name === "settings" && tab === "lists"),
    rulesOn: on(r.name === "settings" && tab === "rules"), rulesCur: cur(r.name === "settings" && tab === "rules"),
    histBadge: waiting ? String(waiting) : "",
    goHistory: () => {
      if (r.name !== "upload") { PR.pendingScroll = "history"; location.hash = "#/upload"; return false; }
      const el = document.getElementById("history");
      if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
      return false;
    },
    connName: c ? `${c.name} · ${c.model || "ยังไม่เลือก model"}` : "ไม่ใช้ AI",
    connState: c ? (c.ready ? "พร้อมใช้" : "ยังไม่พร้อม") : "กฎ + NER + รายชื่อ",
    connFg: c ? (c.ready ? "#166534" : "#92400E") : "#52525B",
    connDot: c ? (c.ready ? "#16A34A" : "#D97706") : "#A1A1AA",
    ttlNote: `ไฟล์ลบอัตโนมัติใน ${PR.ttlMinutes()} นาที`,
  };
}

/* ---------------------------------------------------------------- router */
const ROUTES = [
  [/^\/upload$/, "upload"], [/^\/review\/([0-9a-f]+)$/, "review"],
  [/^\/result\/([0-9a-f]+)\/(redact|highlight)$/, "result"], [/^\/restore$/, "restore"],
  [/^\/(analytics|leaderboard)$/, "analytics"], [/^\/settings\/(conn|lists|rules)$/, "settings"],
];
function route() {
  const path = location.hash.replace(/^#/, "") || "/upload";
  for (const [re, name] of ROUTES) {
    const m = path.match(re);
    if (!m) continue;
    const view = PR.views[name];
    const prev = PR.current;
    const same = PR.route && PR.route.path === path;
    if (prev && prev.leave && !same) prev.leave();
    PR.route = { name, params: m.slice(1), path };
    PR.current = view;
    if (!same) window.scrollTo(0, 0);
    document.title = `${view.title ? view.title(m.slice(1)) + " · " : ""}PikaRedact`;
    view.enter(m.slice(1));
    PR.render();
    return;
  }
  location.replace("#/upload");
}
PR.go = (hash) => { if (location.hash === hash) route(); else location.hash = hash; };

/* ---------------------------------------------------------------- boot */
async function boot() {
  window.addEventListener("dragover", (e) => e.preventDefault());
  window.addEventListener("drop", (e) => e.preventDefault());
  const names = ["shell", "upload", "review", "result", "restore_section", "restore", "settings", "analytics"];
  try {
    const [cfg, ...tpls] = await Promise.all([
      PR.api("api/config"),
      ...names.map((n) => fetch(`static/tpl/${n}.html?v=${window.PR_V || ""}`, { cache: "no-cache" }).then((r) => r.text())),
    ]);
    PR.cfg = cfg;
    names.forEach((n, i) => { PR.tpl[n] = tpls[i]; });
  } catch (e) {
    document.getElementById("view").innerHTML = `<main class="mainp" style="flex:1;margin:10px;background:#fff;border:1px solid #E7E7E2;border-radius:16px;padding:40px"><h1 style="margin:0;font-size:22px">โหลดแอปไม่สำเร็จ</h1><p style="color:#52525B">${esc(e.message)}</p><button type="button" onclick="location.reload()" style="margin-top:8px;border:1px solid #DCDCD6;background:#fff;border-radius:10px;padding:9px 14px;cursor:pointer">ลองใหม่</button></main>`;
    return;
  }
  bind(document.getElementById("side"));
  bind(document.getElementById("view"));
  window.addEventListener("hashchange", route);
  route();
}
PR.boot = boot;
})();
