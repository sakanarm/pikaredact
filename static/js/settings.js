/* Settings: model connectors (from the server's .env), name lists, and detection rules.
   Everything here is shared by all users of this server and saved through /api/settings. */
(() => {
"use strict";
const PR = window.PR;

const TABS = [["conn", "Model connector"], ["lists", "รายชื่อและคำยกเว้น"], ["rules", "การตรวจจับ"]];
const sw = (on, w) => ({ track: on ? "#18181B" : "#D4D4CF", knob: on ? `translateX(${w}px)` : "none" });
const ST_OK = ["#0CA30C", "#166534"], ST_IDLE = ["#C4C4BE", "#71717A"], ST_WARN = ["#FAB219", "#92400E"], ST_BAD = ["#D03B3B", "#B91C1C"];
const NO_CHAT = "ไม่ใช่ chat model";

/* test result cards (same look as the agreed mock) */
const OK_T = { border: "#BBF7D0", bg: "#F0FDF4", dot: "#0CA30C", ink: "#FFFFFF", fg: "#166534", icon: "m5 12 5 5 9-10" };
const WARN_T = { border: "#FDE68A", bg: "#FFFBEB", dot: "#FAB219", ink: "#0B0B0B", fg: "#92400E", icon: "M12 6v8M12 18h.01" };
const BAD_T = { border: "#FECACA", bg: "#FEF2F2", dot: "#D03B3B", ink: "#FFFFFF", fg: "#B91C1C", icon: "M18 6 6 18M6 6l12 12" };
const ERR_HINT = {
  ConnectError: "ติดต่อ Base URL ไม่ได้ ตรวจที่อยู่และเครือข่ายของเซิร์ฟเวอร์",
  ConnectTimeout: "หมดเวลารอการเชื่อมต่อ ตรวจที่อยู่และเครือข่ายของเซิร์ฟเวอร์",
  ReadTimeout: "model ตอบช้าเกินเวลาที่ตั้งไว้ ลองใหม่หรือเลือก model ที่เร็วกว่า",
  HTTPStatusError: "เซิร์ฟเวอร์ปลายทางตอบกลับด้วยข้อผิดพลาด ตรวจ API key และชื่อ model",
  SSLError: "ใบรับรอง TLS ไม่ผ่าน ถ้าเป็น connector ภายใน ให้ตั้ง LLM_VERIFY_TLS=false",
};

const LIST_META = {
  persons: ["รายชื่อพนักงาน", "ชื่อ-นามสกุล ชื่อเล่น ชื่ออังกฤษ"],
  organizations: ["ลูกค้าและองค์กร", "ชื่อบริษัท ชื่อย่อ และชื่อโครงการลับ"],
  allowlist: ["คำยกเว้น", "ไม่ต้องจับ แม้กฎหรือ AI จะคิดว่าเป็นข้อมูลส่วนบุคคล"],
};
const LIST_SHOW = 6, LIST_MAX = 500000;

/* illustrative examples only (fictional) and which layers find each type */
const TYPE_INFO = {
  PERSON: ["สมชาย ใจดี", "NER · รายชื่อ · AI"], ORGANIZATION: ["บริษัท เอบีซี จำกัด", "รายชื่อ · AI"],
  ADDRESS: ["99/1 ถ.พระราม 9", "NER · AI"], PHONE: ["081-234-5678", "กฎ"], EMAIL: ["name@example.com", "กฎ"],
  TH_ID_CARD: ["1-1037-00123-45-8", "กฎ · checksum"], PASSPORT: ["AA1234567", "กฎ · บริบท"],
  BANK_ACCOUNT: ["123-4-56789-0", "กฎ · บริบท"], CREDIT_CARD: ["4111 •••• •••• 1111", "กฎ · Luhn"],
  TH_LICENSE_PLATE: ["1กข 1234", "กฎ"], DATE_OF_BIRTH: ["12/03/2530", "กฎ · บริบท"], IP_ADDRESS: ["10.20.30.41", "กฎ"],
  OTHER_ID: ["EMP-00412", "ตาราง · AI"], SECRET: ["DB_PASSWORD=…", "กฎ"],
};
const TYPE_ORDER = Object.keys(TYPE_INFO);

const S = {
  tab: "conn", sel: null, draft: null, saving: false,
  models: {},   // connector -> { busy, list, error }
  tests: {},    // connector -> { busy, res }
  lists: {},    // list name -> { loading, loaded, content, count, updated, q, editing, draft, busy, error, note }
  savedNote: "", noteTimer: 0,
};

const cfg = () => PR.cfg;
const settings = () => cfg().settings || {};
const profiles = () => cfg().profiles || [];
const defaultName = () => {
  const d = settings().default_connector;
  if (d === "none") return "none";
  if (d && profiles().some((p) => p.name === d)) return d;
  return profiles()[0] ? profiles()[0].name : "none";
};
const savedModel = (name) => {
  const p = profiles().find((x) => x.name === name);
  return (settings().models || {})[name] || (p && p.model) || "";
};

function resetDraft() {
  const models = {};
  for (const p of profiles()) models[p.name] = savedModel(p.name);
  S.draft = { models, def: defaultName() };
}
const isDirty = () => !!S.draft && (S.draft.def !== defaultName() || profiles().some((p) => S.draft.models[p.name] !== savedModel(p.name)));

/* ---------------------------------------------------------------- connectors */
async function loadModels(name) {
  const M = S.models[name] || (S.models[name] = {});
  if (M.busy) return;
  M.busy = true;
  M.error = "";
  PR.render();
  try {
    const r = await PR.api(`api/llm/models?profile=${encodeURIComponent(name)}`);
    M.list = r.models || [];
  } catch (e) {
    M.error = e.message;
  }
  M.busy = false;
  PR.render();
}

async function runTest(name) {
  const T = S.tests[name] || (S.tests[name] = {});
  if (T.busy) return;
  const model = S.draft.models[name];
  if (!model) {
    T.res = Object.assign({ head: "ยังทดสอบไม่ได้", body: "เลือก model ก่อน แล้วกดทดสอบอีกครั้ง" }, WARN_T);
    T.ok = null;
    PR.render();
    return;
  }
  T.busy = true;
  T.res = null;
  PR.render();
  try {
    const r = await PR.api("api/llm/test", PR.json("POST", { profile: name, model }));
    if (r.ok && r.found > 0) {
      T.res = Object.assign({ head: `เชื่อมต่อได้ · ${model} ตอบใน ${r.latency} วินาที`,
        body: `ส่งข้อความสังเคราะห์และได้ JSON ที่ถูกต้อง พบ ${r.found} จาก ${r.expected} จุดที่ตั้งไว้` }, OK_T);
      T.ok = { latency: r.latency };
    } else if (r.ok) {
      T.res = Object.assign({ head: `เชื่อมต่อได้ · ${model} ตอบใน ${r.latency} วินาที แต่ไม่พบข้อมูลที่ตั้งไว้`,
        body: `ได้คำตอบ ${r.items} รายการ แต่ไม่ตรงกับ ${r.expected} จุดในข้อความทดสอบ ลองเลือก model อื่นที่ทำตามคำสั่งได้ดีกว่า` }, WARN_T);
      T.ok = null;
    } else {
      T.res = Object.assign({ head: "เชื่อมต่อไม่ได้", body: `${ERR_HINT[r.error] || "ตรวจการตั้งค่าใน .env ของเซิร์ฟเวอร์แล้วลองใหม่"} (${r.error || "ไม่ทราบสาเหตุ"})` }, BAD_T);
      T.ok = false;
    }
  } catch (e) {
    T.res = Object.assign({ head: "เชื่อมต่อไม่ได้", body: e.message }, BAD_T);
    T.ok = false;
  }
  T.busy = false;
  PR.render();
}

async function saveSettings(patch, note) {
  const s = await PR.api("api/settings", PR.json("PUT", patch));
  cfg().settings = s;
  if (PR.resetUploadDefaults) PR.resetUploadDefaults();
  if (note) {
    S.savedNote = note;
    clearTimeout(S.noteTimer);
    S.noteTimer = setTimeout(() => { S.savedNote = ""; PR.render(); }, 2600);
  }
  return s;
}

async function saveConn() {
  if (S.saving || !isDirty()) return;
  S.saving = true;
  PR.render();
  try {
    await saveSettings({ models: S.draft.models, default_connector: S.draft.def });
    resetDraft();
    PR.toast("บันทึก connector แล้ว · มีผลกับงานถัดไป");
  } catch (e) {
    PR.toast(e.message, true);
  }
  S.saving = false;
  PR.render();
}

function statusOf(p) {
  if (p === "none") return ["พร้อมเสมอ", ...ST_OK];
  const T = S.tests[p.name] || {};
  if (T.ok === false) return ["เชื่อมต่อไม่ได้", ...ST_BAD];
  if (!S.draft.models[p.name]) return ["ยังไม่ได้เลือก model", ...ST_IDLE];
  if (!p.has_key) return ["ยังไม่ได้ตั้ง API key", ...ST_WARN];
  return [T.ok ? `พร้อมใช้ · ${T.ok.latency} s` : "พร้อมใช้", ...ST_OK];
}

function connVals() {
  const D = S.draft;
  const list = profiles();
  if (!S.sel || (S.sel !== "none" && !list.some((p) => p.name === S.sel))) S.sel = D.def;
  const sel = S.sel;
  const conns = [...list, "none"].map((p) => {
    const key = p === "none" ? "none" : p.name;
    const on = key === sel;
    const st = statusOf(p);
    return {
      name: p === "none" ? "ไม่ใช้ AI" : p.name,
      model: p === "none" ? "กฎ + NER + รายชื่อเท่านั้น" : (D.models[key] || "ยังไม่ได้เลือก model"),
      on: String(on), isDefault: key === D.def,
      pick: () => {
        S.sel = key;
        const M = S.models[key];
        if (key !== "none" && !M) loadModels(key);
      },
      border: on ? "#18181B" : "#E7E7E2", ring: on ? "0 0 0 1px #18181B" : "none",
      iconBg: key === "none" ? "#F1F1EE" : "#18181B", iconFg: key === "none" ? "#71717A" : "#FDE047",
      status: st[0], stDot: st[1], stFg: st[2],
    };
  });

  const isNone = sel === "none";
  const p = isNone ? null : list.find((x) => x.name === sel);
  const d = sw(D.def === sel, 16);
  const cur = isNone
    ? { name: "ไม่ใช้ AI", kind: "กฎ + NER + รายชื่อ ทำงานบนเซิร์ฟเวอร์นี้ทั้งหมด", isDefault: String(D.def === sel) }
    : { name: p.name, kind: "LiteLLM / OpenAI-compatible · ตั้งค่าใน .env ของเซิร์ฟเวอร์", isDefault: String(D.def === sel),
      envUrl: p.env_url, envKey: p.env_key, keyMask: p.has_key ? "••••••••••••••••" : "ยังไม่ได้ตั้ง" };

  const M = (!isNone && S.models[sel]) || {};
  const picked = isNone ? "" : D.models[sel];
  let rows = [];
  if (M.list) {
    rows = M.list.map((m) => ({ id: m.id, chat: m.chat }));
    if (picked && !rows.some((r) => r.id === picked)) rows.unshift({ id: picked, chat: true, missing: true });
  }
  const models = rows.map((m, i) => {
    const on = m.id === picked;
    const disabled = !m.chat;
    const saved = m.id === savedModel(sel);
    const note = disabled ? NO_CHAT : m.missing ? "ไม่พบในรายชื่อล่าสุด" : saved ? "chat · ใช้อยู่" : "chat";
    return {
      id: m.id, note, on: String(on), disabled: String(disabled),
      sep: i ? "1px solid #F2F2EE" : "0", bg: on ? "#FAFAF8" : "#FFFFFF", fg: disabled ? "#A1A1AA" : "#18181B",
      cursor: disabled ? "not-allowed" : "pointer", ringC: on ? "#18181B" : "#C4C4BE", dot: on ? "#18181B" : "#FFFFFF",
      pick: () => {
        if (disabled) return false;
        D.models[sel] = m.id;
        if (S.tests[sel]) S.tests[sel] = {};
      },
    };
  });
  const chatN = rows.filter((r) => r.chat && !r.missing).length;
  const noModels = !isNone && !M.list;
  const T = (!isNone && S.tests[sel]) || {};
  const L = cfg().llm || {};
  const tls = sw(L.verify_tls !== false, 16);
  const dirty = isDirty();

  return {
    conns, cur, isNone, hasConn: !isNone,
    defTrack: d.track, defKnob: d.knob,
    makeDefault: () => {
      const first = list[0] ? list[0].name : "none";
      D.def = D.def !== sel ? sel : sel === "none" ? first : "none";
    },
    hasModels: !isNone && !!M.list && rows.length > 0,
    noModels: noModels || (!!M.list && rows.length === 0),
    noModelsHead: M.busy ? "กำลังดึงรายชื่อ model…" : M.error ? "ดึงรายชื่อ model ไม่ได้" : M.list ? "ไม่พบ model ที่ endpoint นี้" : "ยังไม่ได้โหลดรายชื่อ model",
    noModelsBody: M.busy ? "ถาม /v1/models ของ connector นี้" : M.error ? M.error : picked ? `ตอนนี้ใช้ ${picked}` : "กดโหลดรายชื่อ แล้วเลือก chat model",
    noModelsFg: M.error ? "#B91C1C" : "#71717A",
    models,
    modelsSrc: `ดึงจาก /v1/models · ${rows.length} รายการ · ${chatN} chat model · เลือกได้เฉพาะ chat model`,
    modelsBusy: !!M.busy,
    loadLabel: M.busy ? "กำลังโหลด…" : M.list ? "โหลดรายชื่อใหม่" : "โหลดรายชื่อ model",
    loadModels: () => { loadModels(sel); return false; },
    loadDis: M.busy ? "disabled" : "",
    params: [
      ["Temperature", L.temperature != null ? L.temperature : 0, ""], ["ขนาดช่วงข้อความ", L.chunk_chars, "ตัวอักษร"],
      ["ส่งพร้อมกัน", L.concurrency, "คำขอ"], ["Timeout", L.timeout, "วินาที"],
    ].map((x) => ({ label: x[0], v: x[1] == null ? "–" : String(x[1]), unit: x[2] })),
    tlsTrack: tls.track, tlsKnob: tls.knob, tlsText: L.verify_tls !== false ? "เปิด" : "ปิด",
    runTest: () => { runTest(sel); return false; },
    testDis: T.busy ? "disabled" : "", testBusy: !!T.busy, testIdle: !T.busy,
    testLabel: T.busy ? "กำลังทดสอบ…" : "ทดสอบการเชื่อมต่อ",
    tested: !!T.res, test: T.res || {},
    dirty, saveConn: () => { saveConn(); return false; },
    saveDis: !dirty || S.saving ? "disabled" : "", saveLabel: S.saving ? "กำลังบันทึก…" : "บันทึก",
  };
}

/* ---------------------------------------------------------------- lists */
const listNames = () => Object.keys(cfg().lists || {});
const LS = (name) => S.lists[name] || (S.lists[name] = { q: "", draft: "" });

async function loadList(name) {
  const L = LS(name);
  if (L.loading) return;
  L.loading = true;
  PR.render();
  try {
    const r = await PR.api(`api/lists/${encodeURIComponent(name)}`);
    Object.assign(L, { content: r.content || "", count: r.count || 0, updated: r.updated, loaded: true, error: "" });
  } catch (e) {
    L.error = e.message;
    PR.toast(e.message, true);
  }
  L.loading = false;
  PR.render();
}

function parseCsv(text) {
  const rows = [];
  let row = [], cell = "", q = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (q) {
      if (c === '"' && text[i + 1] === '"') { cell += '"'; i++; }
      else if (c === '"') q = false;
      else cell += c;
    } else if (c === '"') q = true;
    else if (c === ",") { row.push(cell); cell = ""; }
    else if (c === "\n" || c === "\r") {
      if (c === "\r" && text[i + 1] === "\n") i++;
      row.push(cell); rows.push(row); row = []; cell = "";
    } else cell += c;
  }
  if (cell || row.length) { row.push(cell); rows.push(row); }
  return rows;
}

async function importFile(name, file) {
  const L = LS(name);
  let text;
  try { text = await file.text(); } catch (_) { PR.toast("อ่านไฟล์ไม่ได้", true); return; }
  text = text.replace(/^﻿/, "");
  const lines = /\.csv$/i.test(file.name)
    ? parseCsv(text).map((r) => r.map((c) => c.trim()).filter(Boolean).join(" | "))
    : text.split(/\r?\n/).map((s) => s.trim());
  if (!L.editing) { L.editing = true; L.draft = L.content || ""; }
  const have = new Set(L.draft.split(/\r?\n/).map((s) => s.trim().toLowerCase()).filter(Boolean));
  const add = [];
  for (const s of lines) {
    const k = s.toLowerCase();
    if (!s || have.has(k)) continue;
    have.add(k);
    add.push(s);
  }
  if (add.length) L.draft = L.draft.replace(/\s*$/, "") + (L.draft.trim() ? "\n" : "") + add.join("\n") + "\n";
  L.note = add.length
    ? `เพิ่ม ${PR.num(add.length)} รายการจาก ${file.name} · ตรวจแล้วกดบันทึก`
    : `ไม่มีรายการใหม่ใน ${file.name} (ซ้ำกับที่มีอยู่แล้วทั้งหมด)`;
  L.error = "";
  PR.render();
}

async function saveList(name) {
  const L = LS(name);
  if (L.busy) return;
  if (new Blob([L.draft]).size > LIST_MAX) { L.error = "รายการยาวเกิน 500 KB แบ่งเป็นหลายส่วนหรือลดรายการที่ไม่จำเป็น"; PR.render(); return; }
  L.busy = true;
  L.error = "";
  PR.render();
  try {
    await PR.api(`api/lists/${encodeURIComponent(name)}`, PR.json("PUT", { content: L.draft }));
    L.editing = false;
    L.note = "";
    L.busy = false;
    PR.toast("บันทึกรายการแล้ว · มีผลกับงานถัดไป");
    await loadList(name);
  } catch (e) {
    L.error = e.message;
    L.busy = false;
  }
  PR.render();
}

function listVals() {
  return listNames().map((name) => {
    const L = LS(name);
    const meta = LIST_META[name] || [name, cfg().lists[name] || ""];
    const all = (L.content || "").split(/\r?\n/).map((s) => s.trim()).filter(Boolean);
    const q = L.q.trim().toLowerCase();
    const hits = q ? all.filter((s) => !s.startsWith("#") && s.toLowerCase().includes(q)) : all;
    const items = hits.slice(0, LIST_SHOW).map((t) => ({ t, fg: t.startsWith("#") ? "#898781" : "#27272A" }));
    return {
      title: meta[0], desc: meta[1], count: L.loaded ? PR.num(L.count) : "…",
      viewing: !L.editing, editing: !!L.editing, loading: !!L.loading && !L.loaded,
      items, more: hits.length > LIST_SHOW ? PR.num(hits.length - LIST_SHOW) : "",
      empty: !!L.loaded && !hits.length, emptyText: q ? `ไม่พบ “${L.q.trim()}”` : "ยังไม่มีรายการ",
      updated: L.updated ? `แก้ไข${/^\d/.test(PR.ago(L.updated)) ? " " : ""}${PR.ago(L.updated)}` : "",
      qid: `lq-${name}`, q: L.q, onQ: (ev) => { L.q = ev.target.value; },
      fid: `lf-${name}`, eid: `le-${name}`,
      pick: () => { const el = document.getElementById(`lf-${name}`); if (el) el.click(); return false; },
      onFile: (ev) => { const f = ev.target.files && ev.target.files[0]; if (f) importFile(name, f); return false; },
      edit: () => { L.editing = true; L.draft = L.content || ""; L.note = ""; L.error = ""; },
      draft: L.draft, onDraft: (ev) => { L.draft = ev.target.value; return false; },
      note: L.note || "", error: L.error || "", busy: !!L.busy, saveDis: L.busy ? "disabled" : "",
      save: () => { saveList(name); return false; },
      cancel: () => { L.editing = false; L.draft = ""; L.note = ""; L.error = ""; },
    };
  });
}

/* ---------------------------------------------------------------- rules */
async function putRule(patch) {
  try {
    await saveSettings(patch, "บันทึกแล้ว");
  } catch (e) {
    PR.toast(e.message, true);
  }
  PR.render();
}

function ruleVals() {
  const st = settings();
  const C = cfg();
  const out = st.default_output || "ask";
  const outDefaults = [["ask", "ถามทุกครั้ง", "เลือกไฮไลต์หรือปิดบังหลังตรวจเสร็จ"], ["redact", "ปิดบัง", "โหมดตามประเภทด้านขวา"],
    ["highlight", "ไฮไลต์อย่างเดียว", "ไฟล์เดิมทำสีจุดที่พบ + รายงาน .csv"]].map((o) => {
    const on = o[0] === out;
    return { label: o[1], desc: o[2], on: String(on), border: on ? "#18181B" : "#E7E7E2", ringC: on ? "#18181B" : "#C4C4BE", dot: on ? "#18181B" : "#FFFFFF",
      pick: () => { if (!on) { st.default_output = o[0]; putRule({ default_output: o[0] }); } } };
  });
  const modes = Object.keys(C.modes || {});
  const mode = st.mode || "token";
  const modeOpts = modes.map((m) => ({ v: m, label: PR.modeLabel(m), sel: m === mode ? "selected" : "" }));

  const marks = [C.min_score, C.auto_accept_score, C.review_score].map((v) => Number(v || 0));
  const pct = (v) => `${Math.round(v * 100)}%`;
  const f2 = (v) => v.toFixed(2);
  const [lo, mid, hi] = marks;
  const thGrad = `linear-gradient(90deg, #E7E7E2 0 ${pct(lo)}, #FDE68A ${pct(lo)} ${pct(mid)}, #FCD34D ${pct(mid)} ${pct(hi)}, #18181B ${pct(hi)} 100%)`;
  const thMarks = marks.map((v) => ({ left: pct(v), v: f2(v) }));
  const thRows = [[`< ${f2(lo)}`, "ไม่แสดง"], [`${f2(lo)}–${f2(mid)}`, "แสดง แต่ไม่เลือกไว้ ให้คนตัดสิน"],
    [`${f2(mid)}–${f2(hi)}`, "เลือกไว้ และติดป้าย “ควรตรวจ”"], [`≥ ${f2(hi)}`, "เลือกไว้ มั่นใจ"]].map((r) => ({ range: r[0], text: r[1] }));

  const off = new Set(st.types_off || []);
  const tm = st.type_modes || {};
  const all = Object.keys(C.entity_types || {});
  const order = [...TYPE_ORDER.filter((t) => all.includes(t) && t !== "SECRET"), ...all.filter((t) => !TYPE_INFO[t]),
    ...(all.includes("SECRET") ? ["SECRET"] : [])];
  let typesOn = 0;
  const types = order.map((t, i) => {
    const locked = t === "SECRET";
    const on = locked || !off.has(t);
    if (on) typesOn++;
    const s = sw(on, 14);
    const info = TYPE_INFO[t] || ["", ""];
    const cur = tm[t] || "";
    return {
      label: PR.type(t).label, color: (C.entity_types[t] || {}).color || PR.type(t).line, ex: info[0], src: info[1],
      on: String(on), locked, unlocked: !locked, sep: i ? "1px solid #F2F2EE" : "0",
      track: locked ? "#52525B" : s.track, knob: s.knob, cursor: locked ? "not-allowed" : "pointer", fg: on ? "#18181B" : "#A1A1AA",
      selFg: cur ? "#18181B" : "#71717A",
      opts: [{ v: "", label: "ตามโหมดที่เลือก", sel: cur ? "" : "selected" }, ...modes.map((m) => ({ v: m, label: PR.modeLabel(m), sel: m === cur ? "selected" : "" }))],
      toggle: () => {
        if (locked) return false;
        const next = on ? [...off, t] : [...off].filter((x) => x !== t);
        st.types_off = next;
        putRule({ types_off: next });
      },
      setMode: (ev) => {
        const v = ev.target.value;
        const next = Object.assign({}, tm);
        if (v) next[t] = v; else delete next[t];
        st.type_modes = next;
        putRule({ type_modes: next });
      },
    };
  });

  return {
    outDefaults, modeOpts, setMode: (ev) => { st.mode = ev.target.value; putRule({ mode: ev.target.value }); },
    thGrad, thMarks, thRows, types, typesOn: String(typesOn), typesAll: String(order.length), savedNote: S.savedNote,
  };
}

/* ---------------------------------------------------------------- view */
function vals() {
  const tabs = TABS.map((t) => {
    const on = t[0] === S.tab;
    return { label: t[1], on: String(on), pick: () => { PR.go(`#/settings/${t[0]}`); return false; },
      w: on ? "600" : "500", fg: on ? "#18181B" : "#71717A", line: on ? "#18181B" : "transparent" };
  });
  const base = {
    tabs, crumb: (TABS.find((t) => t[0] === S.tab) || TABS[0])[1],
    isConn: S.tab === "conn", isLists: S.tab === "lists", isRules: S.tab === "rules",
  };
  if (S.tab === "conn") return Object.assign(base, connVals());
  if (S.tab === "lists") return Object.assign(base, { lists: listVals() });
  return Object.assign(base, ruleVals());
}

PR.views.settings = {
  tpl: "settings",
  title: ([tab]) => (TABS.find((t) => t[0] === tab) || TABS[0])[1],
  enter([tab]) {
    S.tab = tab || "conn";
    if (!S.draft || !isDirty()) resetDraft();
    if (S.tab === "conn") {
      if (!S.sel) S.sel = S.draft.def;
      if (S.sel !== "none" && !S.models[S.sel]) loadModels(S.sel);
    }
    if (S.tab === "lists") for (const n of listNames()) if (!LS(n).loaded) loadList(n);
  },
  vals,
};
})();
