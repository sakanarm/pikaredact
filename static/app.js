/* PikaRedact app — plain JS, relative URLs so it works under any sub-path (e.g. /PikaRedact/). */
"use strict";
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { v == null ? localStorage.removeItem(k) : localStorage.setItem(k, v); } catch { /* private mode */ } },
};

async function api(path, opts = {}) {
  const r = await fetch(path, opts);
  let body = null;
  try { body = await r.json(); } catch { /* not json */ }
  if (!r.ok) throw new Error((body && body.detail) || `HTTP ${r.status}`);
  return body;
}
const jsonOpts = (method, data) => ({ method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });

let CFG = null;
const S = { job: null, accepted: new Set(), added: [], typeOff: new Set(), mapping: null, restoreMap: null };

/* ------------------------------------------------------------ theme & nav */
(function theme() {
  const t = store.get("theme");
  if (t) document.documentElement.dataset.theme = t;
  $("#themeBtn").onclick = () => {
    const dark = getComputedStyle(document.documentElement).colorScheme === "dark";
    const next = dark ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    store.set("theme", next);
  };
})();

function show(view) {
  $$(".view").forEach((v) => v.classList.toggle("hidden", v.id !== `v-${view}`));
  $$(".nav[data-view]").forEach((n) => n.classList.toggle("active", n.dataset.view === view));
  if (view === "analytics") loadStats();
  if (view === "lists") loadList();
  if (view === "settings") renderSettings();
  history.replaceState(null, "", `#${view}`);
}
$$(".nav[data-view]").forEach((n) => (n.onclick = () => show(n.dataset.view)));

/* ------------------------------------------------------------ boot */
async function boot() {
  CFG = await api("api/config");
  const exts = CFG.supported.filter((e) => [".docx", ".xlsx", ".pptx", ".pdf", ".txt", ".csv"].includes(e)).join(" ");
  $("#dropSub").textContent = `${exts} และอื่น ๆ · ไม่เกิน ${CFG.max_upload_mb} MB · ลบอัตโนมัติใน ${CFG.ttl_minutes} นาที`;
  $("#file").accept = CFG.supported.join(",");
  for (const sel of [$("#mode"), $("#sMode")]) sel.innerHTML = Object.entries(CFG.modes).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("");
  renderTypeModes();
  applyScanDefaults();
  const v = location.hash.slice(1);
  const last = store.get("job");
  if (last) await loadJob(last, true);
  show(["scan", "review", "restore", "lists", "analytics", "settings"].includes(v) ? v : "scan");
}

function applyScanDefaults() {
  const s = CFG.settings;
  const n = s.profiles.length;
  $("#useLlm").checked = !!s.use_llm && n > 0;
  $("#useLlm").disabled = n === 0;
  $("#llmSummary").textContent = n ? s.profiles.map((p) => `${p.name}/${p.model}`).join(", ") : "ยังไม่ได้เลือกโมเดล — ตั้งค่าในหน้า ตั้งค่า";
  $(`input[name=combine][value=${s.combine}]`).checked = true;
  $("#minAgree").value = s.min_agree;
  $("#combineRow").classList.toggle("hidden", n < 2);
  $("#mode").value = s.mode;
  $("#sanitize").checked = s.sanitize;
  $("#engineInfo").textContent = n ? `AI: ${n} โมเดล` : "AI: ปิด";
}

/* ------------------------------------------------------------ scan */
let src = "file", pickedFile = null;
$$("#srcTabs button").forEach((b) => (b.onclick = () => {
  src = b.dataset.src;
  $$("#srcTabs button").forEach((x) => x.classList.toggle("on", x === b));
  $("#srcFile").classList.toggle("hidden", src !== "file");
  $("#srcText").classList.toggle("hidden", src !== "text");
}));
const drop = $("#drop");
function pick(f) { pickedFile = f; $("#dropTitle").textContent = f ? `${f.name} · ${(f.size / 1024 / 1024).toFixed(2)} MB` : "ลากไฟล์มาวาง หรือคลิกเพื่อเลือก"; }
$("#file").onchange = (e) => pick(e.target.files[0]);
drop.ondragover = (e) => { e.preventDefault(); drop.classList.add("over"); };
drop.ondragleave = () => drop.classList.remove("over");
drop.ondrop = (e) => { e.preventDefault(); drop.classList.remove("over"); if (e.dataTransfer.files[0]) pick(e.dataTransfer.files[0]); };

const STAGES = { rules: "ตรวจด้วยกฎและ NLP ภาษาไทย", llm: "AI กำลังอ่านเอกสาร", merge: "รวมผลและตรวจซ้ำทั้งเอกสาร" };

$("#scanBtn").onclick = async () => {
  const fd = new FormData();
  if (src === "file") { if (!pickedFile) return alert("กรุณาเลือกไฟล์"); fd.append("file", pickedFile); }
  else { if (!$("#text").value.trim()) return alert("กรุณาวางข้อความ"); fd.append("text", $("#text").value); }
  fd.append("options", JSON.stringify({
    use_llm: $("#useLlm").checked,
    combine: $("input[name=combine]:checked").value,
    min_agree: +$("#minAgree").value || 2,
  }));
  $("#scanBtn").disabled = true;
  $("#progressCard").classList.remove("hidden");
  $("#progErr").classList.add("hidden");
  $("#progStage").textContent = "กำลังอัปโหลดและเปิดไฟล์…";
  $("#progCount").textContent = "";
  try {
    const { id } = await api("api/jobs", { method: "POST", body: fd });
    await poll(id);
  } catch (e) {
    $("#progErr").textContent = e.message;
    $("#progErr").classList.remove("hidden");
  } finally {
    $("#scanBtn").disabled = false;
  }
};

async function poll(id) {
  for (;;) {
    const j = await api(`api/jobs/${id}`);
    if (j.status === "error") throw new Error(j.error);
    if (j.status === "done") { $("#progressCard").classList.add("hidden"); setJob(j); show("review"); return; }
    $("#progStage").textContent = STAGES[j.stage] || "รอคิว…";
    const bar = $("#progBar");
    if (j.total) { bar.classList.remove("indet"); bar.firstElementChild.style.width = `${Math.round((100 * j.done) / j.total)}%`; $("#progCount").textContent = `${j.done}/${j.total} ส่วน`; }
    else { bar.classList.add("indet"); $("#progCount").textContent = ""; }
    await new Promise((r) => setTimeout(r, 700));
  }
}

async function loadJob(id, quiet) {
  try { const j = await api(`api/jobs/${id}`); if (j.status === "done") setJob(j); }
  catch (e) { store.set("job", null); if (!quiet) alert(e.message); }
}

/* ------------------------------------------------------------ review */
function setJob(j) {
  S.job = j;
  S.segIndex = Object.fromEntries(j.segments.map((s, i) => [s.id, i]));
  S.accepted = new Set(j.findings.filter((f) => f.accepted).map((f) => f.id));
  S.added = [];
  S.typeOff = new Set();
  S.mapping = null;
  store.set("job", j.id);
  $("#revTitle").textContent = j.filename;
  $("#revEmpty").classList.add("hidden");
  $("#revBody").classList.remove("hidden");
  $("#exportOut").classList.add("hidden");
  $("#revWarnings").innerHTML = (j.warnings || []).map((w) => `<div class="notice warn">${esc(w)}</div>`).join("");
  const m = j.models || [];
  $("#modelsCard").classList.toggle("hidden", !m.length);
  $("#modelsBox").innerHTML = m.map((x) => `<div class="modelrow"><span class="chip model">${esc(x.model)}</span>
    <span>${x.failed ? '<span style="color:var(--bad)">ตอบไม่ครบ</span> · ' : ""}พบ ${x.found} · ${x.seconds}s</span></div>`).join("");
  const ext = j.highlight_ext !== j.ext ? ` (ไฮไลต์เป็น ${j.highlight_ext})` : "";
  $("input[name=action][value=highlight]").parentElement.querySelector("small").textContent = `ไฟล์เดิม${ext} + สีเหลือง + CSV`;
  renderTypeFilter();
  renderReview();
}

function allFindings() { return S.job.findings.concat(S.added); }
const isOn = (f) => (f.manual ? true : S.accepted.has(f.id));
const typeInfo = (t) => CFG.entity_types[t] || { label: t, color: "#64748b" };

function renderTypeFilter() {
  const counts = {};
  for (const f of allFindings()) counts[f.entity_type] = (counts[f.entity_type] || 0) + 1;
  $("#typeFilter").innerHTML = Object.keys(CFG.entity_types).filter((t) => counts[t]).map((t) =>
    `<span class="chip ${S.typeOff.has(t) ? "off" : ""}" data-t="${t}" style="--c:${typeInfo(t).color}"><span class="sw"></span>${esc(typeInfo(t).label)} ${counts[t]}</span>`).join("")
    || '<span class="muted small">ไม่พบข้อมูลส่วนบุคคล</span>';
  $$("#typeFilter .chip").forEach((c) => (c.onclick = () => {
    S.typeOff.has(c.dataset.t) ? S.typeOff.delete(c.dataset.t) : S.typeOff.add(c.dataset.t);
    renderTypeFilter(); renderReview();
  }));
}

function renderReview() {
  const j = S.job, all = allFindings();
  const visible = all.filter((f) => !S.typeOff.has(f.entity_type));
  const bySeg = {};
  for (const f of visible) (bySeg[f.seg_id] ||= []).push(f);
  const showAll = $("#showAll").checked;
  const segs = (showAll ? j.segments : j.segments.filter((s) => bySeg[s.id])).slice(0, 4000);
  let html = "", group = null;
  for (const s of segs) {
    if (s.group !== group) { group = s.group; if (group) html += `<div class="group-h">${esc(group)}</div>`; }
    html += `<div class="seg"><div class="lab">${esc(s.label)}</div><div class="tx" data-seg="${esc(s.id)}">${markup(s.text, bySeg[s.id] || [])}</div></div>`;
  }
  if (!segs.length) html = `<div class="bd muted">${all.length ? "ไม่มีรายการในประเภทที่เลือก" : "ไม่พบข้อมูลส่วนบุคคลในเอกสารนี้ — ลากคลุมข้อความเพื่อเพิ่มเองได้ (เปิด “แสดงทุกส่วน”)"}</div>`;
  $("#doc").innerHTML = html;

  const order = (f) => (S.segIndex[f.seg_id] ?? 0) * 1e6 + f.start;
  const list = visible.slice().sort((a, b) => order(a) - order(b));
  $("#fCount").textContent = `${all.filter(isOn).length}/${all.length}`;
  $("#flist").innerHTML = list.map((f) => {
    const layers = (f.layers || ["manual"]).map((l) => `<span class="chip">${esc(CFG.layers[l] || l)}</span>`).join("");
    const models = (f.models || []).map((m) => `<span class="chip model" title="พบโดยโมเดลนี้">${esc(m)}</span>`).join("");
    return `<div class="fitem ${isOn(f) ? "" : "off"}" data-id="${esc(f.id)}">
      <input type="checkbox" ${isOn(f) ? "checked" : ""} data-id="${esc(f.id)}">
      <div><div class="v">${esc(f.text)}</div>
        <div class="chips"><span class="chip" style="--c:${typeInfo(f.entity_type).color}"><span class="sw"></span>${esc(typeInfo(f.entity_type).label)}</span>${layers}${models}</div></div>
      <span class="score">${f.manual ? "เพิ่มเอง" : Math.round(f.score * 100) + "%"}</span></div>`;
  }).join("") || '<div class="bd muted small">ไม่มีรายการ</div>';
  const on = all.filter(isOn).length;
  $("#revStats").textContent = `${j.segments.length} ส่วน · พบ ${j.findings.length} · เลือก ${on} · ${j.seconds}s`;
}

function markup(text, fs) {
  fs = fs.slice().sort((a, b) => a.start - b.start || b.end - a.end);
  let out = "", pos = 0;
  for (const f of fs) {
    if (f.start < pos) continue;
    const low = !f.manual && f.score < CFG.review_score ? " low" : "";
    out += esc(text.slice(pos, f.start)) +
      `<mark class="f${isOn(f) ? "" : " off"}${low}" data-id="${esc(f.id)}" style="--c:${typeInfo(f.entity_type).color}" title="${esc(typeInfo(f.entity_type).label)}">${esc(text.slice(f.start, f.end))}</mark>`;
    pos = f.end;
  }
  return out + esc(text.slice(pos));
}

function toggle(id) {
  const m = S.added.find((f) => f.id === id);
  if (m) { S.added = S.added.filter((f) => f !== m); renderTypeFilter(); }
  else S.accepted.has(id) ? S.accepted.delete(id) : S.accepted.add(id);
  const y = $("#doc").scrollTop, fy = $("#flist").scrollTop;
  renderReview();
  $("#doc").scrollTop = y; $("#flist").scrollTop = fy;
}

$("#doc").addEventListener("click", (e) => {
  const mk = e.target.closest("mark.f");
  if (mk && window.getSelection().isCollapsed) toggle(mk.dataset.id);
});
$("#flist").addEventListener("click", (e) => {
  const it = e.target.closest(".fitem");
  if (!it) return;
  if (e.target.matches("input")) return toggle(it.dataset.id);
  const mk = $(`#doc mark[data-id="${CSS.escape(it.dataset.id)}"]`);
  if (mk) { mk.scrollIntoView({ block: "center", behavior: "smooth" }); mk.classList.add("focus"); setTimeout(() => mk.classList.remove("focus"), 1200); }
});
$("#showAll").onchange = renderReview;
$("#allOn").onclick = () => { S.job.findings.forEach((f) => !S.typeOff.has(f.entity_type) && S.accepted.add(f.id)); renderReview(); };
$("#allOff").onclick = () => { S.job.findings.forEach((f) => !S.typeOff.has(f.entity_type) && S.accepted.delete(f.id)); renderReview(); };
$("#newBtn").onclick = () => {
  if (S.job) fetch(`api/jobs/${S.job.id}`, { method: "DELETE" });
  S.job = null; store.set("job", null); pick(null); $("#text").value = "";
  $("#revBody").classList.add("hidden"); $("#revEmpty").classList.remove("hidden");
  show("scan");
};

/* manual add: select text inside one segment */
let addbar = null;
function closeAddbar() { if (addbar) { addbar.remove(); addbar = null; } }
document.addEventListener("mousedown", (e) => { if (addbar && !addbar.contains(e.target)) closeAddbar(); });
$("#doc").addEventListener("mouseup", () => setTimeout(() => {
  const sel = window.getSelection();
  if (sel.isCollapsed || !sel.rangeCount) return;
  const r = sel.getRangeAt(0);
  const tx = r.startContainer.parentElement?.closest(".tx");
  if (!tx || tx !== r.endContainer.parentElement?.closest(".tx")) return;
  const pre = document.createRange();
  pre.selectNodeContents(tx); pre.setEnd(r.startContainer, r.startOffset);
  let start = pre.toString().length, end = start + r.toString().length;
  const seg = S.job.segments[S.segIndex[tx.dataset.seg]];
  while (start < end && /\s/.test(seg.text[start])) start++;
  while (end > start && /\s/.test(seg.text[end - 1])) end--;
  if (end <= start) return;
  const rect = r.getBoundingClientRect();
  closeAddbar();
  addbar = document.createElement("div");
  addbar.className = "addbar";
  addbar.style.left = `${Math.max(8, Math.min(rect.left, innerWidth - 360))}px`;
  addbar.style.top = `${rect.bottom + 6}px`;
  addbar.innerHTML = `<select>${Object.entries(CFG.entity_types).map(([k, v]) => `<option value="${k}" ${k === "PERSON" ? "selected" : ""}>${esc(v.label)}</option>`).join("")}</select><button class="btn sm blue">เพิ่ม</button>`;
  document.body.appendChild(addbar);
  addbar.querySelector("button").onclick = () => {
    const et = addbar.querySelector("select").value;
    S.added.push({ id: `m${Date.now()}`, seg_id: seg.id, start, end, text: seg.text.slice(start, end), entity_type: et, score: 1, manual: true, layers: ["manual"], models: [] });
    // the same value elsewhere in the document: offer to add every occurrence
    const val = seg.text.slice(start, end);
    let more = 0;
    if (val.length >= 2) for (const s of S.job.segments) {
      let i = s.text.indexOf(val);
      while (i >= 0) {
        const taken = allFindings().some((f) => f.seg_id === s.id && f.start < i + val.length && i < f.end);
        if (!taken) { S.added.push({ id: `m${Date.now()}_${more}`, seg_id: s.id, start: i, end: i + val.length, text: val, entity_type: et, score: 1, manual: true, layers: ["manual"], models: [] }); more++; }
        i = s.text.indexOf(val, i + val.length);
      }
    }
    closeAddbar(); sel.removeAllRanges(); renderTypeFilter(); renderReview();
  };
}, 0));

/* ------------------------------------------------------------ export */
$$("input[name=action]").forEach((r) => (r.onchange = () => $("#redactOpts").classList.toggle("hidden", r.value !== "redact" || !r.checked)));
function renderTypeModes() {
  $("#typeModes").innerHTML = Object.entries(CFG.entity_types).map(([k, v]) => `<div class="row" style="justify-content:space-between">
    <span>${esc(v.label)}</span><select data-t="${k}"><option value="">ตามค่าหลัก</option>${Object.entries(CFG.modes).map(([m, l]) => `<option value="${m}">${esc(l)}</option>`).join("")}</select></div>`).join("");
}

$("#exportBtn").onclick = async () => {
  const action = $("input[name=action]:checked").value;
  const type_modes = {};
  $$("#typeModes select").forEach((s) => s.value && (type_modes[s.dataset.t] = s.value));
  const body = {
    action, accepted: [...S.accepted], mode: $("#mode").value, type_modes, sanitize: $("#sanitize").checked,
    added: S.added.map(({ seg_id, start, end, entity_type }) => ({ seg_id, start, end, entity_type })),
  };
  const btn = $("#exportBtn"), out = $("#exportOut");
  btn.disabled = true; btn.textContent = "กำลังสร้างไฟล์…";
  try {
    const r = await api(`api/jobs/${S.job.id}/export`, jsonOpts("POST", body));
    S.mapping = r.mapping;
    let h = "";
    if (r.leaks.length) h += `<div class="notice bad">ยังพบข้อมูลเดิม ${r.leaks.length} จุดในไฟล์ผลลัพธ์: ${r.leaks.slice(0, 5).map((l) => `${esc(l.masked)} (${esc(l.where)})`).join(", ")} — อย่าส่งต่อจนกว่าจะตรวจ</div>`;
    else if (action === "redact") h += `<div class="notice good">ตรวจไฟล์ผลลัพธ์ซ้ำแล้ว ไม่พบข้อมูลเดิมที่เลือกไว้</div>`;
    h += r.warnings.map((w) => `<div class="notice warn">${esc(w)}</div>`).join("");
    h += `<div class="files">${r.files.map((f) => `<a href="api/jobs/${S.job.id}/files/${encodeURIComponent(f.name)}" download>⬇ ${esc(f.name)}</a>`).join("")}</div>`;
    if (r.mapping && Object.keys(r.mapping).length) {
      h += `<div class="stack" style="gap:8px;border-top:1px solid var(--line);padding-top:12px">
        <b class="small">ไฟล์คืนค่า (.piimap)</b>
        <span class="small muted">ตั้งรหัสผ่านเพื่อเข้ารหัสในเบราว์เซอร์ — เก็บไว้เอง เซิร์ฟเวอร์ไม่เก็บไว้</span>
        <div class="row"><input class="input" type="password" id="pmPass" placeholder="รหัสผ่านอย่างน้อย 8 ตัว" style="flex:1"><button class="btn sm" id="pmBtn">ดาวน์โหลด .piimap</button></div></div>`;
    }
    out.innerHTML = h;
    out.classList.remove("hidden");
    const pm = $("#pmBtn");
    if (pm) pm.onclick = () => downloadMap($("#pmPass").value);
  } catch (e) {
    out.innerHTML = `<div class="notice bad">${esc(e.message)}</div>`;
    out.classList.remove("hidden");
  } finally {
    btn.disabled = false; btn.textContent = "สร้างไฟล์";
  }
};

/* ------------------------------------------------------------ .piimap (WebCrypto: PBKDF2 → AES-GCM) */
const b64 = (u8) => btoa(String.fromCharCode(...new Uint8Array(u8)));
const unb64 = (s) => Uint8Array.from(atob(s), (c) => c.charCodeAt(0));
async function deriveKey(pass, salt, iter) {
  const base = await crypto.subtle.importKey("raw", new TextEncoder().encode(pass), "PBKDF2", false, ["deriveKey"]);
  return crypto.subtle.deriveKey({ name: "PBKDF2", salt, iterations: iter, hash: "SHA-256" }, base, { name: "AES-GCM", length: 256 }, false, ["encrypt", "decrypt"]);
}
function save(blob, name) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob); a.download = name; a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 2000);
}
async function downloadMap(pass) {
  if (!window.crypto?.subtle) return alert("เบราว์เซอร์ไม่อนุญาตการเข้ารหัสบนหน้าที่ไม่ใช่ HTTPS — เปิดผ่าน https:// แล้วลองใหม่");
  if (!pass || pass.length < 8) return alert("รหัสผ่านต้องมีอย่างน้อย 8 ตัวอักษร");
  const salt = crypto.getRandomValues(new Uint8Array(16)), iv = crypto.getRandomValues(new Uint8Array(12)), iter = 310000;
  const key = await deriveKey(pass, salt, iter);
  const data = await crypto.subtle.encrypt({ name: "AES-GCM", iv }, key, new TextEncoder().encode(JSON.stringify({ mapping: S.mapping })));
  const file = { format: "piimap", v: 1, kdf: "PBKDF2-SHA256", iter, cipher: "AES-256-GCM", salt: b64(salt), iv: b64(iv), data: b64(data), created: new Date().toISOString(), count: Object.keys(S.mapping).length };
  const stem = S.job.filename.replace(/\.[^.]+$/, "");
  save(new Blob([JSON.stringify(file)], { type: "application/json" }), `${stem}.piimap`);
}

$("#mapOpen").onclick = async () => {
  const f = $("#mapFile").files[0];
  if (!f) return alert("เลือกไฟล์ .piimap");
  if (!window.crypto?.subtle) return alert("ต้องเปิดผ่าน HTTPS เพื่อถอดรหัส");
  try {
    const p = JSON.parse(await f.text());
    const key = await deriveKey($("#mapPass").value, unb64(p.salt), p.iter);
    const plain = await crypto.subtle.decrypt({ name: "AES-GCM", iv: unb64(p.iv) }, key, unb64(p.data));
    S.restoreMap = JSON.parse(new TextDecoder().decode(plain)).mapping;
    $("#mapInfo").textContent = `เปิดแล้ว · ${Object.keys(S.restoreMap).length} รหัส`;
  } catch { S.restoreMap = null; $("#mapInfo").textContent = "เปิดไม่ได้ — รหัสผ่านไม่ถูกต้องหรือไฟล์เสีย"; }
};
$("#restoreBtn").onclick = () => {
  if (!S.restoreMap) return alert("เปิดไฟล์ .piimap ก่อน");
  let t = $("#restoreIn").value;
  for (const tok of Object.keys(S.restoreMap).sort((a, b) => b.length - a.length)) t = t.split(tok).join(S.restoreMap[tok]);
  $("#restoreOut").value = t;
};

/* ------------------------------------------------------------ lists */
let listName = null;
async function loadList(name) {
  const names = Object.keys(CFG.lists);
  listName = name || listName || names[0];
  $("#listTabs").innerHTML = names.map((n) => `<button class="${n === listName ? "on" : ""}" data-n="${n}">${esc({ persons: "บุคคล", organizations: "องค์กร", allowlist: "คำยกเว้น" }[n] || n)}</button>`).join("");
  $$("#listTabs button").forEach((b) => (b.onclick = () => loadList(b.dataset.n)));
  $("#listHelp").textContent = CFG.lists[listName];
  $("#listMsg").textContent = "";
  $("#listText").value = (await api(`api/lists/${listName}`)).content;
}
$("#listSave").onclick = async () => {
  await api(`api/lists/${listName}`, jsonOpts("PUT", { content: $("#listText").value }));
  $("#listMsg").textContent = "บันทึกแล้ว — มีผลกับการตรวจครั้งถัดไป";
};

/* ------------------------------------------------------------ settings */
let rows = [];
function renderSettings() {
  const s = CFG.settings;
  rows = s.profiles.map((p) => ({ ...p, on: true }));
  for (const p of CFG.profiles) if (!rows.some((r) => r.name === p.name)) rows.push({ ...p, on: false });
  $("#sUseLlm").checked = s.use_llm; $("#sCombine").value = s.combine; $("#sMinAgree").value = s.min_agree;
  $("#sMode").value = s.mode; $("#sSanitize").checked = s.sanitize; $("#setMsg").textContent = "";
  drawConns();
}
const modelCache = {};
async function models(name) {
  if (!modelCache[name]) modelCache[name] = api(`api/llm/models?profile=${encodeURIComponent(name)}`).then((r) => r.models.filter((m) => m.chat !== false).map((m) => m.id)).catch(() => null);
  return modelCache[name];
}
function drawConns() {
  if (!CFG.profiles.length) { $("#conns").innerHTML = '<div class="muted">ยังไม่มี connector — เพิ่ม LLM_&lt;NAME&gt;_BASE_URL / _API_KEY / _MODEL ในไฟล์ .env ของเซิร์ฟเวอร์</div>'; return; }
  $("#conns").innerHTML = rows.map((r, i) => `<div class="conn" data-i="${i}">
      <label class="check"><input type="checkbox" class="on" ${r.on ? "checked" : ""}><span class="name">${esc(r.name)}</span></label>
      <div class="row"><select class="model"><option>${esc(r.model)}</option></select><button class="btn sm ghost dup" title="ใช้ connector นี้กับอีกโมเดล">+ โมเดล</button></div>
      <div class="row"><span class="small muted res"></span><button class="btn sm test">ทดสอบ</button></div></div>`).join("");
  $$("#conns .conn").forEach(async (el) => {
    const r = rows[+el.dataset.i];
    el.querySelector(".on").onchange = (e) => (r.on = e.target.checked);
    const sel = el.querySelector(".model");
    sel.onchange = () => (r.model = sel.value);
    el.querySelector(".dup").onclick = () => { rows.splice(+el.dataset.i + 1, 0, { name: r.name, model: r.model, on: true }); drawConns(); };
    el.querySelector(".test").onclick = async () => {
      const res = el.querySelector(".res");
      res.textContent = "กำลังทดสอบ…";
      try {
        const t = await api("api/llm/test", jsonOpts("POST", { profile: r.name, model: r.model }));
        res.innerHTML = t.ok ? `<span style="color:var(--good)">ใช้ได้</span> · ${t.latency}s · พบ ${t.found}/${t.expected}` : `<span style="color:var(--bad)">ไม่สำเร็จ</span> ${esc(t.error || "")}`;
      } catch (e) { res.innerHTML = `<span style="color:var(--bad)">${esc(e.message)}</span>`; }
    };
    const list = await models(r.name);
    if (list && list.length) sel.innerHTML = [...new Set([r.model, ...list])].filter(Boolean).map((m) => `<option ${m === r.model ? "selected" : ""}>${esc(m)}</option>`).join("");
  });
}
$("#setSave").onclick = async () => {
  const seen = new Set();
  const profiles = rows.filter((r) => r.on && !seen.has(`${r.name}/${r.model}`) && seen.add(`${r.name}/${r.model}`)).map(({ name, model }) => ({ name, model }));
  CFG.settings = await api("api/settings", jsonOpts("PUT", {
    profiles, use_llm: $("#sUseLlm").checked, combine: $("#sCombine").value, min_agree: +$("#sMinAgree").value || 2,
    mode: $("#sMode").value, sanitize: $("#sSanitize").checked,
  }));
  applyScanDefaults();
  $("#setMsg").textContent = `บันทึกแล้ว · ใช้ ${CFG.settings.profiles.length} โมเดล`;
};

/* ------------------------------------------------------------ analytics */
$("#days").onchange = loadStats;
function bars(obj, labels = {}) {
  const e = Object.entries(obj).sort((a, b) => b[1] - a[1]);
  if (!e.length) return '<div class="muted small">ยังไม่มีข้อมูล</div>';
  const max = e[0][1] || 1;
  return `<div class="bars">${e.map(([k, v]) => `<div class="bar" title="${esc(labels[k] || k)}: ${v}"><span>${esc(labels[k] || k)}</span><span class="track"><i style="width:${(100 * v) / max}%"></i></span><span class="val">${v.toLocaleString()}</span></div>`).join("")}</div>`;
}
async function loadStats() {
  const d = await api(`api/stats?days=${$("#days").value}`);
  const types = Object.fromEntries(Object.entries(CFG.entity_types).map(([k, v]) => [k, v.label]));
  const days = Object.entries(d.per_day), max = Math.max(1, ...days.map((x) => x[1]));
  const card = (t, body) => `<div class="card"><div class="hd"><h2>${t}</h2></div><div class="bd">${body}</div></div>`;
  $("#stats").innerHTML = `
    <div class="tiles">
      <div class="card tile"><div class="k">เอกสารที่ตรวจ</div><div class="n">${d.scans.toLocaleString()}</div></div>
      <div class="card tile"><div class="k">ข้อมูลที่พบ</div><div class="n">${d.findings.toLocaleString()}</div></div>
      <div class="card tile"><div class="k">ไฟล์ที่สร้าง</div><div class="n">${d.exports.toLocaleString()}</div></div>
      <div class="card tile"><div class="k">เวลาตรวจ (มัธยฐาน)</div><div class="n">${d.median_seconds}s</div></div>
      <div class="card tile"><div class="k">หลุดหลังปิดบัง</div><div class="n" style="color:${d.leaks ? "var(--bad)" : "inherit"}">${d.leaks}</div></div>
    </div>
    ${card("เอกสารต่อวัน", days.length ? `<div class="cols">${days.map(([k, v]) => `<i style="height:${(100 * v) / max}%" data-tip="${k}: ${v}"></i>`).join("")}</div>
      <div class="row small muted" style="justify-content:space-between;margin-top:6px"><span>${days[0][0]}</span><span>${days[days.length - 1][0]}</span></div>` : '<div class="muted small">ยังไม่มีข้อมูล</div>')}
    <div class="grid2">
      ${card("ประเภทข้อมูลที่พบ", bars(d.by_type, types))}
      ${card("พบด้วยวิธี", bars(d.by_layer, CFG.layers))}
      ${card("โมเดล AI ที่ใช้", bars(d.by_model))}
      ${card("วิธีส่งออก", bars(d.by_mode, { highlight: "ไฮไลต์", ...CFG.modes }))}
      ${card("ชนิดไฟล์", bars(d.by_ext))}
    </div>`;
}

boot().catch((e) => { document.body.insertAdjacentHTML("afterbegin", `<div class="notice bad" style="margin:12px">${esc(e.message)}</div>`); });
