/* Result screen (downloads, key file, before/after, post-export checks) and the
   "restore from tokens" tool, which is shared by the result screen and its own page. */
(() => {
"use strict";
const PR = window.PR;

const MONO = "'IBM Plex Mono', monospace";
const PLAIN = { bg: "transparent", fg: "inherit", font: "inherit", size: "1em", pad: "0", sh: "none" };
const GOOD = { bg: "#0CA30C", ink: "#FFFFFF", labelFg: "#006300", ok: true, warn: false, level: "ผ่าน" };
const WARN = { bg: "#FAB219", ink: "#0B0B0B", labelFg: "#92400E", ok: false, warn: true, level: "ระวัง" };
const CRIT = { bg: "#D03B3B", ink: "#FFFFFF", labelFg: "#B91C1C", ok: false, warn: true, level: "สำคัญ" };
const OFFICE = new Set([".docx", ".xlsx", ".pptx", ".pdf"]);
const TOKEN_RE = /\[[A-Z_]+_\d+\]/g;

/* ================================================================ restore (shared) */
const newRestore = (useJob) => ({ text: "", fileName: "", fileText: "", pass: "", useJob: !!useJob, busy: false, error: "", result: null, cache: null });

async function runRestore(R, jobMapping) {
  if (R.busy) return;
  R.error = "";
  if (!R.text.trim()) { R.error = "วางข้อความจาก AI ก่อน"; PR.render(); return; }
  let mapping = null;
  if (R.useJob && jobMapping) mapping = jobMapping;
  else {
    if (!R.fileText) { R.error = "เลือกไฟล์กุญแจ .piimap"; PR.render(); return; }
    if (!R.pass) { R.error = "ใส่รหัสผ่านของไฟล์กุญแจ"; PR.render(); return; }
    if (R.cache && R.cache.file === R.fileText && R.cache.pass === R.pass) mapping = R.cache.mapping;
  }
  R.busy = true;
  PR.render();
  try {
    if (!mapping) {
      mapping = await PR.decryptMap(R.fileText, R.pass);
      R.cache = { file: R.fileText, pass: R.pass, mapping };
    }
    R.result = PR.restoreParts(R.text, mapping);
  } catch (e) {
    R.error = e.message;
    R.result = null;
  }
  R.busy = false;
  PR.render();
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
  } catch (_) {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.cssText = "position:fixed;left:-9999px;top:0";
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand("copy"); } catch (__) { /* nothing else to try */ }
    ta.remove();
  }
  PR.toast("คัดลอกข้อความที่คืนค่าแล้ว");
}

/* vals for tpl/restore_section.html. jobMapping: the key of the job on screen (memory only), or null */
PR.restoreVals = (R, jobMapping, margin) => {
  const canJob = !!jobMapping;
  if (!canJob) R.useJob = false;
  const jobKey = canJob && R.useJob;
  const n = (R.text.match(TOKEN_RE) || []).length;
  const res = R.result;
  return {
    rsMargin: margin,
    rsText: R.text,
    rsOnText: (ev) => { R.text = ev.target.value; R.result = null; R.error = ""; },
    rsTokenNote: !R.text ? "รองรับข้อความหลายย่อหน้า · รหัสที่ไม่อยู่ในไฟล์กุญแจจะถูกคงไว้ตามเดิม"
      : n ? `พบรหัส ${PR.num(n)} ตัวในข้อความ` : "ยังไม่พบรหัสอย่าง [PERSON_1] ในข้อความ",
    rsJobKey: jobKey, rsFileMode: !jobKey, rsCanJob: canJob,
    rsUseFile: () => { R.useJob = false; R.result = null; R.error = ""; },
    rsUseJob: () => { R.useJob = true; R.result = null; R.error = ""; },
    rsPick: () => { const el = document.getElementById("rs-file"); if (el) el.click(); return false; },
    rsOnFile: (ev) => {
      const file = ev.target.files && ev.target.files[0];
      if (!file) return false;
      file.text().then((t) => {
        R.fileName = file.name;
        R.fileText = t;
        R.cache = null;
        R.result = null;
        R.error = "";
        try {
          const p = JSON.parse(t);
          if (!p || p.format !== "piimap") throw new Error();
        } catch (_) { R.error = "ไฟล์นี้ไม่ใช่ไฟล์กุญแจ .piimap"; }
        PR.render();
      });
      ev.target.value = "";
      return false;
    },
    rsFileName: R.fileName || "เลือกไฟล์กุญแจ .piimap",
    rsFileBorder: R.fileName ? "#A1A1AA" : "#DCDCD6",
    rsFileFg: R.fileName ? "#18181B" : "#71717A",
    rsPass: R.pass,
    rsOnPass: (ev) => { R.pass = ev.target.value; R.error = ""; },
    rsKey: (ev) => { if (ev.key === "Enter") { ev.preventDefault(); runRestore(R, jobMapping); } return false; },
    rsRun: () => { runRestore(R, jobMapping); return false; },
    rsRunDis: R.busy ? "disabled" : "",
    rsBusy: R.busy, rsIdle: !R.busy,
    rsRunLabel: R.busy ? "กำลังคืนค่า…" : "คืนค่า",
    rsError: R.error,
    rsDone: !!res, rsNotDone: !res,
    rsSummary: !res ? "" : !res.hit && !res.miss ? "ไม่พบรหัสในข้อความ"
      : `คืนค่า ${PR.num(res.hit)} รหัส · ${res.miss ? `พบรหัสที่ไม่รู้จัก ${PR.num(res.miss)}` : "ไม่พบรหัสที่ไม่รู้จัก"}`,
    rsSumFg: res && res.miss ? "#92400E" : "#166534",
    rsSumIc: res && res.miss ? "#D97706" : "#0CA30C",
    rsParts: res ? res.parts.map((p) => (p.restored ? { t: p.t, sh: "inset 0 -2px 0 #0CA30C", bg: "transparent", tip: "คืนค่าแล้ว" }
      : p.unknown ? { t: p.t, sh: "none", bg: "#FEF3C7", tip: "ไม่พบรหัสนี้ในไฟล์กุญแจ" }
      : { t: p.t, sh: "none", bg: "transparent", tip: "" })) : [],
    rsCopy: () => { if (res) copyText(res.text); return false; },
    rsSaveTxt: () => { if (res) PR.save(new Blob([res.text], { type: "text/plain;charset=utf-8" }), "restored.txt"); return false; },
  };
};

/* ================================================================ result */
const states = {};
let S = null, action = "redact", tick = 0;

function stateFor(id) {
  if (!states[id]) states[id] = { id, job: null, loading: true, gone: false, goneText: "", pw: "", keyBusy: false, keySaved: false,
    keyNote: "", keyNoteFg: "#71717A", mapFetching: false, rs: newRestore(true) };
  return states[id];
}

const summaryOf = (st, a) => (st.job && st.job.outputs && st.job.outputs[a]) || (PR.results[st.id] && PR.results[st.id][a] && PR.results[st.id][a].summary) || null;
const mappingOf = (st) => (PR.results[st.id] && PR.results[st.id].redact && PR.results[st.id].redact.mapping) || null;

async function load(st) {
  const rv = PR.review[st.id];
  if (!st.job && rv && rv.loaded && rv.job) { st.job = rv.job; st.loading = false; }
  try {
    const j = await PR.api(`api/jobs/${st.id}`);
    if (S !== st) return;
    if (j.status !== "done") { location.replace(`#/review/${st.id}`); return; }
    st.job = j;
    st.loading = false;
    st.gone = false;
    if (!j.outputs || !j.outputs[action]) {   // nothing exported for this action yet: review it first
      PR.reviewState(st.id).output = action;
      location.replace(`#/review/${st.id}`);
      return;
    }
    ensureMapping(st);
  } catch (e) {
    if (S !== st) return;
    st.loading = false;
    st.gone = true;
    st.goneText = e.status === 404
      ? `ไฟล์และผลตรวจถูกลบอัตโนมัติหลังครบ ${PR.ttlMinutes()} นาที เพื่อไม่ให้ข้อมูลค้างบนเซิร์ฟเวอร์ อัปโหลดไฟล์เดิมเพื่อตรวจใหม่ได้`
      : e.message;
    if (e.status === 404) PR.hist.put(st.id, { expires: 0 });
  }
  PR.render();
}

/* after a page reload the key is gone from memory; rebuild it from the same request (tokens are deterministic) */
async function ensureMapping(st) {
  const sum = summaryOf(st, "redact");
  if (!sum || !sum.has_mapping || mappingOf(st) || st.mapFetching) return mappingOf(st);
  st.mapFetching = true;
  try {
    const q = sum.request || {};
    const r = await PR.api(`api/jobs/${st.id}/preview`, PR.json("POST",
      { accepted: q.accepted || [], added: q.added || [], mode: q.mode || sum.mode, type_modes: q.type_modes || {}, with_mapping: true }));
    PR.results[st.id] = PR.results[st.id] || {};
    PR.results[st.id].redact = { summary: sum, mapping: r.mapping || {} };
  } catch (_) { /* the key card shows the error when saving */ }
  st.mapFetching = false;
  if (S === st) PR.render();
  return mappingOf(st);
}

async function saveKey(st, mainName) {
  const hint = PR.passHint(st.pw);
  if (!hint.ok || st.keyBusy) return;
  st.keyBusy = true;
  st.keyNote = "";
  PR.render();
  try {
    const mapping = await ensureMapping(st);
    if (!mapping) throw new Error("โหลดกุญแจของงานนี้ไม่ได้ ลองรีเฟรชหน้าอีกครั้ง");
    const file = await PR.encryptMap(mapping, st.pw);
    const stem = mainName.replace(/\.redacted\.[^.]+$/, "") || "document";
    PR.save(new Blob([JSON.stringify(file, null, 1)], { type: "application/json" }), `${stem}.piimap`);
    st.keySaved = true;
    st.pw = "";
    st.keyNote = "บันทึกไฟล์กุญแจแล้ว · เก็บรหัสผ่านแยกจากไฟล์ ถ้าลืมรหัสผ่านจะเปิดไม่ได้";
    st.keyNoteFg = "#166534";
    PR.hist.put(st.id, { downloaded: true });
  } catch (e) {
    st.keyNote = e.message;
    st.keyNoteFg = "#B91C1C";
  }
  st.keyBusy = false;
  PR.render();
}

function goAction(st, a) {
  if (a === action) return;
  if (st.job && st.job.outputs && st.job.outputs[a]) PR.go(`#/result/${st.id}/${a}`);
  else { PR.reviewState(st.id).output = a; PR.go(`#/review/${st.id}`); }
}

const effMode = (sum, t) => (t === "SECRET" ? "remove" : ((sum.request || {}).type_modes || {})[t] || sum.mode || "token");

/* the paragraph with the most chosen findings, cut around them if long */
function sample(st, sum) {
  const j = st.job;
  if (!j || !j.segments) return null;
  const segs = {};
  j.segments.forEach((s, i) => { segs[s.id] = Object.assign({ idx: i }, s); });
  const req = sum.request || {};
  const ids = new Set(req.accepted || []);
  const chosen = (j.findings || []).filter((f) => ids.has(f.id)).map((f) => ({ id: f.id, seg_id: f.seg_id, start: f.start, end: f.end, t: f.entity_type }));
  for (const a of req.added || []) {
    const s = segs[a.seg_id];
    if (s && a.start >= 0 && a.end <= s.text.length && a.start < a.end) chosen.push({ id: a.id, seg_id: a.seg_id, start: a.start, end: a.end, t: a.entity_type });
  }
  if (!chosen.length) return null;
  const by = {};
  for (const f of chosen) (by[f.seg_id] = by[f.seg_id] || []).push(f);
  let best = null;
  for (const sid of Object.keys(by)) {
    if (!segs[sid]) continue;
    if (!best || by[sid].length > by[best].length || (by[sid].length === by[best].length && segs[sid].idx < segs[best].idx)) best = sid;
  }
  if (!best) return null;
  const seg = segs[best];
  const list = by[best].sort((a, b) => a.start - b.start || b.end - a.end);
  const text = seg.text;
  let from = 0, to = text.length;
  if (text.length > 520) {
    from = Math.max(0, list[0].start - 120);
    to = Math.min(text.length, from + 520);
    for (const f of list) if (f.start < to && f.end > to) to = f.end;
  }
  const before = [], after = [];
  const plain = (t) => { before.push(Object.assign({}, PLAIN, { text: t })); after.push(Object.assign({}, PLAIN, { text: t })); };
  if (from > 0) plain("…");
  let cur = from;
  for (const f of list) {
    if (f.start < cur || f.end > to) continue;
    if (f.start > cur) plain(text.slice(cur, f.start));
    const orig = text.slice(f.start, f.end);
    before.push(Object.assign({}, PLAIN, { text: orig, bg: "#F1F1EE", pad: "1px 3px" }));
    if (sum.action === "highlight") after.push(Object.assign({}, PLAIN, { text: orig, bg: "#FDE047", fg: "#18181B", pad: "1px 3px" }));
    else {
      const m = effMode(sum, f.t);
      const rep = (sum.replaced || {})[f.id];
      const out = rep != null ? rep : "████";
      if (m === "token") after.push({ text: out, bg: "#18181B", fg: "#FDE047", font: MONO, size: "0.84em", pad: "2px 5px", sh: "none" });
      else if (m === "mask") after.push({ text: out, bg: "#F1F1EE", fg: "#3F3F46", font: MONO, size: "0.9em", pad: "1px 3px", sh: "none" });
      else if (m === "fake") after.push(Object.assign({}, PLAIN, { text: out, fg: "#14532D", sh: "inset 0 -2px 0 #86EFAC" }));
      else after.push({ text: out, bg: "#18181B", fg: "#18181B", font: "inherit", size: "1em", pad: "1px 3px", sh: "none" });
    }
    cur = f.end;
  }
  if (cur < to) plain(text.slice(cur, to));
  if (to < text.length) plain("…");
  return { before, after, label: `${seg.label || "ข้อความ"}${from > 0 || to < text.length ? " · ตัดมาบางส่วน" : ""}` };
}

function tokenRange(sum, t) {
  const re = new RegExp(`^\\[${t}_(\\d+)\\]$`);
  const nums = new Set();
  for (const v of Object.values(sum.replaced || {})) { const m = re.exec(v); if (m) nums.add(+m[1]); }
  if (!nums.size) return `[${t}_n]`;
  const a = [...nums].sort((x, y) => x - y);
  return a.length === 1 ? `[${t}_${a[0]}]` : `[${t}_${a[0]}…${a[a.length - 1]}]`;
}

function descOf(st, sum, i, name) {
  const j = st.job;
  const hl = sum.action === "highlight";
  if (/\.report\.html$/.test(name || "")) {
    return "รายงานหลักฐานสำหรับ DPO · ชุดกฎหมายที่ใช้ จำนวนต่อประเภท และ SHA-256 ของไฟล์ ไม่มีค่าข้อมูล · เปิดแล้วพิมพ์เป็น PDF ได้";
  }
  if (i > 0) {   // second file is always the findings report
    return hl ? "ประเภท ตำแหน่ง คะแนน แหล่งที่มา · ค่าแสดงแบบปิดบางส่วน" : "รายการที่ปิดบัง · ค่าแสดงแบบปิดบางส่วน ไม่มีค่าเต็ม";
  }
  if (hl) {
    const same = (j.highlight_ext || j.ext) === j.ext;
    return `${same ? "ไฟล์เดิม" : `ไฟล์ ${PR.extLabel(j.highlight_ext)}`} ไฮไลต์สีเหลือง ${PR.num(sum.count)} จุด · ยังมีข้อมูลจริง`;
  }
  return {
    ".docx": "รูปแบบเดิม · ตาราง หัว/ท้ายกระดาษ และคอมเมนต์ถูกปิดบังด้วย",
    ".xlsx": "รูปแบบเดิม · ทุกชีตและทุกช่องที่พบถูกปิดบัง",
    ".pptx": "รูปแบบเดิม · สไลด์ โน้ต และแม่แบบที่พบถูกปิดบัง",
    ".pdf": "รูปแบบเดิม · ข้อความถูกลบออกจากไฟล์จริง ไม่ใช่แค่ทับสี",
  }[j.ext] || "รูปแบบเดิม · ข้อความถูกแทนที่ในไฟล์จริง";
}

function checksOf(st, sum) {
  const c = sum.checks || {};
  const out = [];
  const add = (lv, label, detail) => out.push(Object.assign({}, lv, { label, detail }));
  if (sum.action === "highlight") {
    const rejected = Math.max(0, (sum.found || 0) - ((sum.count || 0) - (sum.manual || 0)));
    add(GOOD, `ไฮไลต์ ${PR.num(sum.count)} จุด จาก ${PR.num(sum.found)} จุดที่พบ`,
      (rejected ? `${PR.num(rejected)} จุดที่คุณยกเลิกไม่ถูกไฮไลต์` : "ไม่มีจุดที่ยกเลิก") + (sum.manual ? ` · เพิ่มเอง ${PR.num(sum.manual)} จุด` : ""));
    add(CRIT, "ไฟล์นี้ยังมีข้อมูลจริงทุกจุด",
      c.secrets ? `รวมรหัสผ่าน ${PR.num(c.secrets)} จุด ใช้ภายในทีม ห้ามส่งให้ AI ภายนอก` : "ใช้ภายในทีม ห้ามส่งให้ AI ภายนอก");
    add(GOOD, "รายงาน .csv ไม่มีค่าเต็ม", "แสดงค่าแบบปิดบางส่วนพร้อมตำแหน่ง ให้เจ้าของเอกสารตามแก้");
  } else {
    const leaks = sum.leaks || [];
    if (!leaks.length) add(GOOD, "ไม่พบค่าเดิมหลงเหลือ", `อ่านไฟล์ผลลัพธ์ซ้ำทั้งไฟล์ พบ 0 จาก ${PR.num(c.values)} ค่า`);
    else add(CRIT, `พบค่าเดิมหลงเหลือ ${PR.num(leaks.length)} จุด`,
      leaks.slice(0, 3).map((l) => `${PR.type(l.type).label} ${l.masked} · ${l.where}`).join(" · ") + (leaks.length > 3 ? ` · และอีก ${leaks.length - 3}` : "") + " — เปิดไฟล์ตรวจก่อนส่งต่อ");
    if (c.stem_changed) add(GOOD, "เปลี่ยนชื่อไฟล์เป็น document", "ชื่อไฟล์เดิมมีข้อมูลที่พบ จึงไม่ใช้ชื่อเดิม");
    else add(GOOD, "ชื่อไฟล์ไม่มีข้อมูลส่วนบุคคล", "ตรวจชื่อไฟล์กับค่าที่พบทั้งหมด");
    if (OFFICE.has(st.job.ext)) {
      if (c.sanitize) add(GOOD, "ล้าง metadata และคอมเมนต์แล้ว", st.job.ext === ".pdf" ? "ชื่อผู้สร้าง โปรแกรม และข้อมูลไฟล์" : "ชื่อผู้เขียน บริษัท และ Track Changes");
      else add(WARN, "ไม่ได้ล้าง metadata", "ชื่อผู้เขียนและข้อมูลไฟล์ยังอยู่ · เปิดได้ในตัวเลือกก่อนตรวจ");
    }
    if (c.secrets) add(GOOD, `ลบรหัสผ่าน ${PR.num(c.secrets)} จุด`, "ไม่ถูกบันทึกในไฟล์กุญแจ");
  }
  for (const w of sum.warnings || []) add(WARN, w, "เปิดดูไฟล์ก่อนส่งต่อ");
  return out;
}

function rowsOf(sum) {
  const hl = sum.action === "highlight";
  return Object.entries(sum.counts || {}).sort((a, b) => b[1] - a[1]).map(([t, n]) => {
    const ty = PR.type(t);
    let out, bg, fg;
    if (hl) { out = t === "SECRET" ? "ไฮไลต์ · ค่าจริง" : "ไฮไลต์"; bg = "#FDE047"; fg = "#18181B"; }
    else {
      const m = effMode(sum, t);
      if (m === "token") { out = tokenRange(sum, t); bg = "#18181B"; fg = "#FDE047"; }
      else if (m === "fake") { out = "ข้อมูลปลอม"; bg = "#DCFCE7"; fg = "#14532D"; }
      else { out = m === "mask" ? "ปิดบางส่วน" : "ลบทิ้ง"; bg = "#F1F1EE"; fg = "#3F3F46"; }
    }
    return { label: ty.label, color: ty.line, n: PR.num(n), out, outBg: bg, outFg: fg };
  });
}

function vals() {
  const st = S;
  const id = st.id;
  const j = st.job;
  const name = (j && j.filename) || ((PR.hist.get(id) || {}).name) || "ผลลัพธ์";
  const base = { reviewHref: `#/review/${id}`, filename: name, loading: st.loading && !j, gone: st.gone, goneText: st.goneText };
  const sum = !st.gone && j ? summaryOf(st, action) : null;
  if (!sum) return Object.assign(base, { ready: false, ttlText: "" });

  const mins = Math.max(0, Math.ceil((j.expires - Date.now() / 1000) / 60));
  const hl = sum.action === "highlight";
  const leaks = (sum.leaks || []).length;
  const outputs = j.outputs || {};
  const outs = [["redact", "#18181B"], ["highlight", "#FDE047"]].map(([a, swc]) => {
    const on = a === action, has = !!outputs[a];
    return {
      label: a === "redact" ? (has ? `ปิดบัง · ${PR.modeLabel(outputs.redact.mode)}` : "ปิดบัง") : "ไฮไลต์อย่างเดียว",
      sw: swc, status: has ? "พร้อม" : "ยังไม่สร้าง", stFg: has ? "#166534" : "#71717A",
      on: String(on), w: on ? "600" : "500", bg: on ? "#FFFFFF" : "transparent", fg: on ? "#18181B" : "#52525B",
      sh: on ? "0 1px 2px rgba(0,0,0,.08), 0 0 0 1px #E4E4DF" : "none",
      pick: () => { goAction(st, a); return false; },
    };
  });
  const files = (sum.files || []).map((f, i) => {
    const ext = PR.extLabel((f.name.match(/\.[^.]+$/) || [""])[0]);
    const primary = i === 0;
    return {
      ext, color: PR.extColor(ext), name: f.name, desc: descOf(st, sum, i, f.name), size: PR.size(f.size), url: f.url,
      sep: i ? "1px solid #F2F2EE" : "0",
      btnBg: primary ? "#18181B" : "#FFFFFF", btnFg: primary ? "#FFFFFF" : "#18181B", btnBorder: primary ? "0" : "1px solid #DCDCD6",
      mark: () => { PR.hist.put(id, { downloaded: true }); },
    };
  });
  const smp = sample(st, sum);
  const hint = PR.passHint(st.pw);
  const showKey = !hl && !!sum.has_mapping;
  const nTypes = Object.keys(sum.counts || {}).length;
  const secs = sum.seconds || 0;
  const typeModes = Object.values((sum.request || {}).type_modes || {}).filter((m) => m && m !== sum.mode);
  const mainName = files[0] ? files[0].name : "document";
  const jobMap = showKey ? mappingOf(st) : null;

  return Object.assign(base, {
    ready: true,
    ttlText: mins > 0 ? `ไฟล์และผลตรวจจะถูกลบใน ${mins} นาที` : "ไฟล์หมดเวลาแล้ว · อัปโหลดใหม่เพื่อสร้างอีกครั้ง",
    heroBg: !hl && leaks ? "#FEE2E2" : "#DCFCE7", heroInk: !hl && leaks ? "#B91C1C" : "#166534",
    heroPath: !hl && leaks ? "M12 6v8M12 18h.01" : "m5 12 5 5 9-10",
    title: hl ? "ไฟล์ไฮไลต์พร้อมดาวน์โหลด" : leaks ? "ไฟล์ที่ปิดบังแล้ว · ตรวจก่อนส่งต่อ" : "ไฟล์ที่ปิดบังแล้วพร้อมดาวน์โหลด",
    sub: hl ? `ไฮไลต์ ${PR.num(sum.count)} จุด · ${PR.num(nTypes)} ประเภท · ไฟล์เดิมไม่ถูกแก้ไขเนื้อหา`
      : `ปิดบัง ${PR.num(sum.count)} จุด · โหมด${PR.modeLabel(sum.mode)}${typeModes.length ? " + ตั้งแยกบางประเภท" : ""} · ใช้เวลา ${secs < 1 ? "ไม่ถึง 1" : Math.round(secs)} วินาที`,
    outs, files,
    showKey,
    pwValue: st.pw, onPw: (ev) => { st.pw = ev.target.value; st.keyNote = ""; },
    pwKey: (ev) => { if (ev.key === "Enter") { ev.preventDefault(); saveKey(st, mainName); } return false; },
    pwHint: hint.text, pwHintFg: hint.fg,
    pwBorder: st.pw && !hint.ok ? "#FCA5A5" : "#DCDCD6", pwRing: "none",
    saveKey: () => { saveKey(st, mainName); return false; },
    keyDis: !hint.ok || st.keyBusy ? "disabled" : "",
    keyBusy: st.keyBusy,
    keyLabel: st.keyBusy ? "กำลังเข้ารหัส…" : st.keySaved ? "บันทึกอีกไฟล์" : "บันทึกไฟล์กุญแจ",
    keyNote: st.keyNote, keyNoteFg: st.keyNoteFg,
    hasSample: !!smp, sampleLabel: smp ? smp.label : "", before: smp ? smp.before : [], after: smp ? smp.after : [],
    afterLabel: hl ? "ไฟล์ไฮไลต์" : "ไฟล์ที่ปิดบัง",
    checkTitle: hl ? "ข้อควรรู้ก่อนส่งต่อ" : "ตรวจซ้ำหลังปิดบัง",
    checkSub: hl ? "ไฟล์ไฮไลต์ใช้สำหรับตามแก้ ไม่ใช่สำหรับส่งออก" : "ทำอัตโนมัติทุกครั้งก่อนให้ดาวน์โหลด",
    checks: checksOf(st, sum),
    rows: rowsOf(sum), hasRows: nTypes > 0, noRows: nTypes === 0,
    isHighlight: hl,
    toRedact: () => { goAction(st, "redact"); return false; },
    toRedactTitle: outputs.redact ? "ดูไฟล์ที่ปิดบังแล้ว" : "ปิดบังต่อเพื่อส่งออกหรือส่งให้ AI",
    toRedactSub: outputs.redact ? "สร้างไว้แล้วจากรายการเดียวกัน" : `ใช้รายการเดิม ${PR.num(sum.count)} จุด ไม่ต้องตรวจใหม่`,
    showRestore: showKey,
  }, showKey ? PR.restoreVals(st.rs, jobMap, "16px") : {});
}

PR.views.result = {
  tpl: "result",
  title: () => "ผลลัพธ์",
  enter([id, a]) {
    action = a;
    S = stateFor(id);
    S.keyNote = "";
    clearInterval(tick);
    tick = setInterval(() => PR.render(), 30000);
    load(S);
  },
  leave() { clearInterval(tick); },
  vals,
};

/* ================================================================ restore page */
const RP = newRestore(false);
PR.views.restore = {
  tpl: "restore",
  title: () => "คืนค่าจากรหัส",
  enter() {},
  vals: () => PR.restoreVals(RP, null, "24px"),
};
})();
