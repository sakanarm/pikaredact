/* Review screen: poll the scan, show the document with clickable marks, let people
   accept/reject findings, add missed ones by selecting text, preview the output and export. */
(() => {
"use strict";
const PR = window.PR;
const esc = PR.esc;

const DIGIT_TYPES = new Set(["PHONE", "TH_ID_CARD", "CREDIT_CARD", "BANK_ACCOUNT", "PASSPORT"]);
const LAYER_ORDER = ["rule", "list", "ner", "table", "llm", "propagate", "manual"];
const SRC = { rule: "กฎ", list: "รายชื่อ", ner: "NER · คำนำหน้า", table: "ตาราง", llm: "AI", propagate: "ค่าซ้ำ", manual: "เพิ่มเอง" };
const KIND_LABEL = { header: "หัวกระดาษ", footer: "ท้ายกระดาษ", comment: "คอมเมนต์", note: "โน้ตผู้บรรยาย", metadata: "METADATA",
  hidden: "ส่วนที่ซ่อน", deleted: "ข้อความที่ถูกลบ (ยังอยู่ในไฟล์)", link: "ลิงก์", chart: "แผนภูมิ", field: "ฟิลด์" };
const STAGE = { "": "รอคิว", rules: "ตรวจด้วยกฎ checksum และรายชื่อ", llm: "ให้ AI ภายในองค์กรอ่านเอกสาร", merge: "รวมผลและตัดค่าซ้ำ" };
const SEG_ON = "0 1px 2px rgba(0,0,0,.08), 0 0 0 1px #E4E4DF";
const MONO = "font-family:'IBM Plex Mono',monospace;";
const SECTION_MAX = 80, TABLE_MAX = 60;

let S = null;   // state of the job on screen; every job keeps its own in PR.review[id]
let keyHandler = null;

function stateFor(id) {
  if (!PR.review[id]) {
    const st = (PR.cfg.settings || {});
    PR.review[id] = {
      id, job: null, error: "", loaded: false, timer: 0, t0: Date.now(),
      segs: [], segById: {}, findings: [], manual: [], on: {}, mseq: 0,
      output: st.default_output === "redact" || st.default_output === "highlight" ? st.default_output : null,
      mode: st.mode || "token", view: "source", onlyReview: false, typeFilter: "",
      replaced: {}, pvKey: "", pvWant: "", pvTimer: 0, pvSeq: 0, pvLoading: false,
      pop: null, popFocus: false, expanded: new Set(), docNode: null, docVer: 0, builtVer: -1,
      exporting: false, exportError: "",
      layout: "text", pg: { src: null, srcItems: "", srcBusy: false, srcErr: "", out: null, outKey: "", outWant: "", outBusy: false,
        outErr: "", outSeq: 0, outTimer: 0 }, pageNode: null, pageVer: 0, pageBuilt: -1,
    };
  }
  return PR.review[id];
}

/* ---------------------------------------------------------------- data */
async function poll(st) {
  clearTimeout(st.timer);
  try {
    const j = await PR.api(`api/jobs/${st.id}`);
    st.job = j;
    st.error = "";
    if (j.status === "done") {
      if (!st.loaded) load(st, j);
    } else if (j.status !== "error") {
      st.timer = setTimeout(() => { if (S === st) poll(st); }, 700);
    }
  } catch (e) {
    st.error = e.message;
    if (e.status === 404) { st.gone = true; PR.hist.put(st.id, { expires: 0 }); }
  }
  if (S === st) PR.render();
}

function load(st, j) {
  st.segs = j.segments || [];
  st.segById = {};
  st.segs.forEach((s, i) => { s.idx = i; st.segById[s.id] = s; });
  st.findings = j.findings || [];
  for (const f of st.findings) if (!(f.id in st.on)) st.on[f.id] = f.accepted !== false;
  st.loaded = true;
  st.layout = j.page_view ? "page" : "text";
  st.docVer++;
  PR.hist.put(st.id, { name: j.filename, ext: j.ext, kind: j.kind, created: j.created, expires: j.expires, pii: st.findings.length });
}

const items = (st) => st.findings.concat(st.manual);
const isReview = (f) => !f.manual && f.score < PR.cfg.review_score;
const effMode = (st, t) => (t === "SECRET" ? "remove" : ((PR.cfg.settings || {}).type_modes || {})[t] || st.mode);

function normKey(f) {
  const t = String(f.text).replace(/­/g, "");
  const v = DIGIT_TYPES.has(f.entity_type) && /\d/.test(t) ? t.replace(/[^0-9A-Za-z]/g, "").toUpperCase()
    : t.replace(/\s+/g, " ").trim().toLowerCase();
  return `${f.entity_type}|${v}`;
}
function order(st, f) {
  const s = st.segById[f.seg_id];
  return (s ? s.idx : 0) * 1e6 + f.start;
}

function groupsOf(st) {
  const map = new Map();
  for (const f of items(st).slice().sort((a, b) => order(st, a) - order(st, b))) {
    const k = normKey(f);
    if (!map.has(k)) map.set(k, { key: k, type: f.entity_type, text: f.text, items: [] });
    map.get(k).items.push(f);
  }
  return [...map.values()].map((g) => {
    const on = g.items.some((f) => st.on[f.id]);
    const score = Math.max(...g.items.map((f) => (f.manual ? 1 : f.score)));
    const layers = new Set(), models = new Set();
    g.items.forEach((f) => { (f.layers || [f.layer]).forEach((l) => layers.add(l)); (f.models || []).forEach((m) => models.add(m)); });
    if (layers.size > 1) layers.delete("propagate");
    const src = LAYER_ORDER.filter((l) => layers.has(l))
      .map((l) => (l === "llm" && models.size > 1 ? `AI ${models.size} ตัว` : SRC[l] || l)).join(" + ");
    return Object.assign(g, {
      on, score, src: g.type === "SECRET" ? `${src} · ลบเสมอ` : src,
      review: g.items.every((f) => isReview(f)), manual: g.items.every((f) => f.manual),
    });
  });
}

function payload(st) {
  return {
    accepted: st.findings.filter((f) => st.on[f.id]).map((f) => f.id),
    added: st.manual.filter((f) => st.on[f.id]).map((f) => ({ id: f.id, seg_id: f.seg_id, start: f.start, end: f.end, entity_type: f.entity_type })),
    mode: st.mode, type_modes: (PR.cfg.settings || {}).type_modes || {},
  };
}

/* ---------------------------------------------------------------- manual additions */
function guessType(t) {
  const d = t.replace(/\D/g, "");
  const numeric = /^[\d\s()+.-]+$/.test(t);
  if (/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(t)) return "EMAIL";
  if (/^\d{1,3}(\.\d{1,3}){3}$/.test(t)) return "IP_ADDRESS";
  if (numeric && d.length === 13) return "TH_ID_CARD";
  if (numeric && /^(0|66)/.test(d) && d.length >= 9 && d.length <= 11) return "PHONE";
  if (numeric && d.length >= 15 && d.length <= 16) return "CREDIT_CARD";
  if (numeric && d.length >= 10 && d.length <= 12) return "BANK_ACCOUNT";
  if (/^[A-Z]{1,2}\d{6,8}$/i.test(t)) return "PASSPORT";
  if (/(บริษัท|จำกัด|มหาชน|ธนาคาร|มหาวิทยาลัย|โรงพยาบาล|โรงเรียน|มูลนิธิ|สมาคม|\bCo\.|\bLtd|\bInc\b|\bCorp)/i.test(t)) return "ORGANIZATION";
  if (/(ถนน|ถ\.|ซอย|ซ\.|ตำบล|ต\.|อำเภอ|อ\.|จังหวัด|จ\.|แขวง|เขต|หมู่ที่|ม\.\d|เลขที่|\b\d{5}\b)/.test(t) && t.length > 10) return "ADDRESS";
  if (!/\s/.test(t) && t.length >= 12 && /[A-Za-z]/.test(t) && /\d/.test(t)) return "SECRET";
  if (/\d/.test(t)) return "OTHER_ID";
  return "PERSON";
}
function overlaps(st, segId, s, e) {
  return items(st).some((f) => f.seg_id === segId && f.start < e && s < f.end);
}
function occurrences(st, text) {
  const out = [];
  if (!text) return out;
  for (const seg of st.segs) {
    const t = seg.text || "";
    for (let i = t.indexOf(text); i >= 0; i = t.indexOf(text, i + text.length)) {
      if (!overlaps(st, seg.id, i, i + text.length)) out.push({ seg_id: seg.id, start: i, end: i + text.length });
    }
  }
  return out;
}
function txOf(node) {
  const el = node && (node.nodeType === 1 ? node : node.parentElement);
  return el && el.closest ? el.closest(".tx[data-seg]") : null;
}
function onSelect(st) {
  if (st.view !== "source") return;
  const sel = window.getSelection();
  if (!sel || sel.isCollapsed || !sel.rangeCount) {
    if (st.pop) { st.pop = null; PR.render(); }
    return;
  }
  const r = sel.getRangeAt(0);
  const tx = txOf(r.startContainer), tx2 = txOf(r.endContainer);
  if (!tx || tx !== tx2) {
    if (tx || tx2) PR.toast("เลือกได้ทีละย่อหน้าหรือทีละช่องของตาราง");
    return;
  }
  const pre = document.createRange();
  pre.setStart(tx, 0);
  pre.setEnd(r.startContainer, r.startOffset);
  const seg = st.segById[tx.dataset.seg];
  if (!seg) return;
  let s = pre.toString().length, e = s + r.toString().length;
  while (s < e && /\s/.test(seg.text[s])) s++;
  while (e > s && /\s/.test(seg.text[e - 1])) e--;
  if (e <= s) return;
  if (overlaps(st, seg.id, s, e)) {
    PR.toast("ช่วงนี้มีรายการที่พบอยู่แล้ว คลิกที่ไฮไลต์เพื่อเลือกหรือยกเลิก");
    return;
  }
  const text = seg.text.slice(s, e);
  const rect = r.getBoundingClientRect();
  const card = tx.closest("section.card");
  const box = card.getBoundingClientRect();
  const below = rect.bottom + 210 < window.innerHeight;
  st.pop = {
    seg_id: seg.id, start: s, end: e, text, type: guessType(text), all: false, occ: occurrences(st, text),
    top: Math.round(below ? rect.bottom - box.top + 8 : rect.top - box.top - 196),
    left: Math.round(Math.max(8, Math.min(rect.left - box.left, box.width - 308))),
  };
  st.popFocus = true;
  PR.render();
}
function addManual(st) {
  const p = st.pop;
  if (!p) return;
  const spots = p.all && p.occ.length ? p.occ : [{ seg_id: p.seg_id, start: p.start, end: p.end }];
  for (const sp of spots) {
    if (overlaps(st, sp.seg_id, sp.start, sp.end)) continue;
    const id = `m${++st.mseq}`;
    st.manual.push({ id, seg_id: sp.seg_id, start: sp.start, end: sp.end, entity_type: p.type, text: p.text,
      score: 1, sources: ["manual"], layer: "manual", layers: ["manual"], models: [], manual: true });
    st.on[id] = true;
  }
  st.pop = null;
  st.docVer++;
  const sel = window.getSelection();
  if (sel) sel.removeAllRanges();
  PR.toast(spots.length > 1 ? `เพิ่ม ${spots.length} จุดแล้ว` : "เพิ่มแล้ว");
}

/* ---------------------------------------------------------------- document node */
function segHTML(st, seg, bySeg) {
  const t = seg.text || "";
  let h = "", pos = 0;
  for (const f of bySeg[seg.id] || []) {
    if (f.start < pos || f.end > t.length) continue;
    f.src = t.slice(f.start, f.end);
    h += esc(t.slice(pos, f.start)) + `<span class="pii" role="button" tabindex="0" data-f="${esc(f.id)}">${esc(f.src)}</span>`;
    pos = f.end;
  }
  return `<span class="tx" data-seg="${esc(seg.id)}">${h + esc(t.slice(pos))}</span>`;
}

function colName(n) {   // 1 -> A, 27 -> AA
  let s = "";
  for (; n > 0; n = Math.floor((n - 1) / 26)) s = String.fromCharCode(65 + ((n - 1) % 26)) + s;
  return s || "A";
}

function sectionsOf(st) {
  const secs = [];
  let cur = null;
  for (const seg of st.segs) {
    const has = (seg.cells || []).length;
    if (!has && !(seg.text || "").trim()) continue;
    const key = `${seg.group}\u0001${seg.kind}`;
    if (!cur || cur.key !== key) { cur = { key, group: seg.group || "เอกสาร", kind: seg.kind, segs: [] }; secs.push(cur); }
    cur.segs.push(seg);
  }
  return secs;
}

function buildDoc(st) {
  const node = st.docNode || document.createElement("div");
  if (!st.docNode) {
    node.className = "docv";
    node.addEventListener("click", (ev) => {
      const more = ev.target.closest(".doc-more");
      if (more) {
        const i = +more.dataset.sec;
        if (st.expanded.has(i)) st.expanded.delete(i); else st.expanded.add(i);
        st.docVer++;
        PR.render();
        return;
      }
      const m = ev.target.closest(".pii");
      if (m && !(window.getSelection() || {}).toString()) toggleItem(st, m.dataset.f);
    });
    node.addEventListener("keydown", (ev) => {
      const m = ev.target.closest && ev.target.closest(".pii");
      if (m && (ev.key === "Enter" || ev.key === " ")) { ev.preventDefault(); toggleItem(st, m.dataset.f); }
    });
    node.addEventListener("mouseup", () => setTimeout(() => onSelect(st), 0));
    st.docNode = node;
  }
  const bySeg = {};
  for (const f of items(st)) (bySeg[f.seg_id] = bySeg[f.seg_id] || []).push(f);
  Object.values(bySeg).forEach((a) => a.sort((x, y) => x.start - y.start || y.end - x.end));
  const secs = sectionsOf(st);
  const firstOfGroup = {};
  let html = "";
  secs.forEach((sec, i) => {
    if (!(sec.group in firstOfGroup)) firstOfGroup[sec.group] = i;
    const big = sec.segs.length > SECTION_MAX && !st.expanded.has(i);
    const head = sec.kind === "body" || sec.kind === "table" ? sec.group : `${sec.group} · ${KIND_LABEL[sec.kind] || sec.kind}`;
    let body = "", hidden = 0, tableRows = 0;
    const tables = {};
    const blocks = [];
    for (const seg of sec.segs) {
      const cells = seg.cells || [];
      if (cells.length) {
        for (const [key, r, c] of cells) {
          if (!tables[key]) { tables[key] = { rows: {}, cols: new Set() }; blocks.push({ table: key }); }
          const t = tables[key];
          ((t.rows[r] = t.rows[r] || {})[c] = t.rows[r][c] || []).push(seg);
          t.cols.add(c);
        }
      } else {
        blocks.push({ seg });
      }
    }
    for (const b of blocks) {
      if (b.seg) {
        const seg = b.seg;
        if (big && !bySeg[seg.id]) { hidden++; continue; }
        const chip = seg.kind !== "body" && seg.kind !== "table" && seg.label ? `<span class="kind">${esc(seg.label)}</span>` : "";
        body += `<p class="doc-p">${chip}${segHTML(st, seg, bySeg)}</p>`;
        continue;
      }
      const t = tables[b.table];
      const rows = Object.keys(t.rows).map(Number).sort((x, y) => x - y);
      let cols = [...t.cols].sort((x, y) => x - y);
      tableRows = Math.max(tableRows, rows.length);
      const cut = (big || rows.length > TABLE_MAX) && !st.expanded.has(i);
      if (b.table.startsWith("xlsx:")) {
        // spreadsheet: draw it like Excel — column letters, row numbers, empty columns kept in place
        const base = b.table === "xlsx:csv" ? 1 : 0;   // csv rows/cols are 0-based, xlsx refs are 1-based
        const lo = cols[0], hi = cols[cols.length - 1];
        if (hi - lo < 60) cols = Array.from({ length: hi - lo + 1 }, (_, k) => lo + k);
        let tr = "", prev = null;
        rows.forEach((r, ri) => {
          const row = t.rows[r];
          const hit = cols.some((c) => (row[c] || []).some((seg) => bySeg[seg.id]));
          if (cut && ri > 0 && !hit) { hidden++; return; }
          const gap = prev != null && r !== prev + 1 ? " class=\"xgap\"" : "";
          prev = r;
          tr += `<tr${gap}><th class="xr" scope="row">${r + base}</th>${cols.map((c) => {
            const segs = row[c] || [];
            const num = segs.length && segs.every((seg) => /^[\s\d.,%()+\-]+$/.test(seg.text || ""));
            const long = segs.some((seg) => (seg.text || "").length > 40);
            const cls = [num && "num", long && "wrap"].filter(Boolean).join(" ");
            return `<td${cls ? ` class="${cls}"` : ""}>${segs.map((seg) => segHTML(st, seg, bySeg)).join("\n")}</td>`;
          }).join("")}</tr>`;
        });
        const sheet = b.table === "xlsx:csv" ? ((st.job && st.job.filename) || "CSV") : sec.group.replace(/^ชีต\s*/, "");
        body += `<div class="xl-tab">${esc(sheet)}</div><div class="xl-wrap"><table class="xl"><thead><tr><th class="xr"></th>${cols.map((c) => `<th scope="col">${colName(c + base)}</th>`).join("")}</tr></thead><tbody>${tr}</tbody></table></div>`;
        continue;
      }
      let th = "", tb = "";
      rows.forEach((r, ri) => {
        const row = t.rows[r];
        const hit = cols.some((c) => (row[c] || []).some((seg) => bySeg[seg.id]));
        if (cut && ri > 0 && !hit) { hidden++; return; }
        const tag = ri === 0 ? "th" : "td";
        const tr = `<tr>${cols.map((c) => `<${tag}>${(row[c] || []).map((seg) => segHTML(st, seg, bySeg)).join("\n")}</${tag}>`).join("")}</tr>`;
        if (ri === 0) th = tr; else tb += tr;
      });
      body += `<div style="overflow-x:auto"><table class="doc-tbl"><thead>${th}</thead><tbody>${tb}</tbody></table></div>`;
    }
    const canFold = sec.segs.length > SECTION_MAX || tableRows > TABLE_MAX;
    if (hidden) body += `<button type="button" class="doc-more" data-sec="${i}">แสดงส่วนที่ไม่มีข้อมูลส่วนบุคคลอีก ${PR.num(hidden)} ส่วน</button>`;
    else if (canFold && st.expanded.has(i)) body += `<button type="button" class="doc-more" data-sec="${i}">ย่อเหลือเฉพาะส่วนที่พบข้อมูล</button>`;
    if (!body) body = `<p class="doc-p" style="color:#A1A1AA">(ไม่มีข้อความ)</p>`;
    const sheetOnly = blocks.length && blocks.every((x) => x.table && x.table.startsWith("xlsx:"));   // the sheet tab already names it
    html += `<section class="doc-sec" data-sec="${i}" style="margin-top:${i ? 26 : 0}px">${sheetOnly ? "" : `<h3>${esc(head)}</h3>`}${body}</section>`;
  });
  node.innerHTML = html || `<p class="doc-p" style="color:#71717A">ไม่พบข้อความในไฟล์นี้</p>`;
  st.firstOfGroup = firstOfGroup;
  st.builtVer = st.docVer;
  node.__css = null;
}

function markOf(st, f, on) {
  const t = PR.type(f.entity_type);
  const orig = f.src != null ? f.src : f.text;
  const dim = st.typeFilter && st.typeFilter !== f.entity_type ? "opacity:.35;" : "";
  if (st.view === "preview" && st.output) {
    if (!on) return { css: `background:transparent;color:#27272A;padding:0;border-radius:0;${dim}`, text: orig };
    if (st.output === "highlight") return { css: `background:#FDE047;color:#18181B;${dim}`, text: orig };
    const mode = effMode(st, f.entity_type);
    const fresh = st.pvKey && Object.prototype.hasOwnProperty.call(st.replaced, f.id);
    const text = fresh ? st.replaced[f.id] : mode === "remove" ? "████" : "…";
    if (mode === "remove") return { css: `background:#18181B;color:#18181B;${dim}`, text };
    if (mode === "token") return { css: `background:#18181B;color:#FDE047;${MONO}font-size:.84em;${dim}`, text };
    if (mode === "mask") return { css: `background:#F1F1EE;color:#3F3F46;${MONO}font-size:.9em;${dim}`, text };
    return { css: `background:transparent;color:#14532D;box-shadow:inset 0 -2px 0 #86EFAC;border-radius:0;${dim}`, text };
  }
  if (!on) return { css: `background:transparent;color:#71717A;outline:1px dashed #C4C4BE;outline-offset:-1px;text-decoration:line-through;${dim}`, text: orig };
  if (isReview(f)) return { css: `background:${t.bg};color:${t.fg};outline:1px dashed ${t.line};outline-offset:-1px;${dim}`, text: orig };
  return { css: `background:${t.bg};color:${t.fg};box-shadow:inset 0 -2px 0 ${t.line};${dim}`, text: orig };
}

function paintMarks(st) {
  const node = st.docNode;
  if (!node) return;
  const byId = {};
  for (const f of items(st)) byId[f.id] = f;
  node.querySelectorAll(".pii").forEach((el) => {
    const f = byId[el.dataset.f];
    if (!f) return;
    const on = !!st.on[f.id];
    const m = markOf(st, f, on);
    if (el.__css !== m.css) { el.style.cssText = m.css; el.__css = m.css; }
    if (el.textContent !== m.text) el.textContent = m.text;
    const title = `${PR.type(f.entity_type).label} · ${f.manual ? "เพิ่มเอง" : `ความมั่นใจ ${f.score.toFixed(2)}`} · ${on ? "เลือกอยู่" : "ไม่ได้เลือก"} — คลิกเพื่อสลับ`;
    if (el.title !== title) el.title = title;
    el.setAttribute("aria-pressed", on ? "true" : "false");
  });
}

function toggleItem(st, id) {
  if (!(id in st.on)) return;
  st.on[id] = !st.on[id];
  PR.render();
}

function locate(st, f) {
  if (usePages(st) && !(st.view === "preview" && st.output) && st.pageNode) {
    const b = st.pageNode.querySelector(`.pbox[data-ids~="${CSS.escape(f.id)}"]`);
    if (b) {
      b.scrollIntoView({ behavior: "smooth", block: "center" });
      b.classList.remove("flash");
      void b.offsetWidth;
      b.classList.add("flash");
      b.focus({ preventScroll: true });
      return;
    }
    st.layout = "text";   // not on a page (comment, note, metadata...): show it in the text view
    PR.render();
    setTimeout(() => locate(st, f), 60);
    return;
  }
  if (!st.docNode) return;
  const el = st.docNode.querySelector(`[data-f="${CSS.escape(f.id)}"]`);
  if (!el) return;
  el.scrollIntoView({ behavior: "smooth", block: "center" });
  el.classList.remove("flash");
  void el.offsetWidth;
  el.classList.add("flash");
  el.focus({ preventScroll: true });
}

/* ---------------------------------------------------------------- preview */
function schedulePreview(st) {
  if (!(st.view === "preview" && st.output === "redact" && st.loaded)) return;
  const body = payload(st);
  const key = JSON.stringify(body);
  if (key === st.pvKey) {
    if (st.pvWant !== key) { st.pvWant = key; st.pvSeq++; clearTimeout(st.pvTimer); st.pvLoading = false; }
    return;
  }
  if (key === st.pvWant) return;
  st.pvWant = key;
  clearTimeout(st.pvTimer);
  st.pvTimer = setTimeout(async () => {
    const seq = ++st.pvSeq;
    st.pvLoading = true;
    if (S === st) PR.render();
    try {
      const r = await PR.api(`api/jobs/${st.id}/preview`, PR.json("POST", body));
      if (seq !== st.pvSeq) return;
      st.replaced = r.replaced || {};
      st.pvKey = key;
    } catch (e) {
      if (seq !== st.pvSeq) return;
      st.pvWant = "";
      PR.toast(e.message, true);
    }
    st.pvLoading = false;
    if (S === st) PR.render();
  }, 150);
}

/* ---------------------------------------------------------------- page view (Word / PowerPoint / PDF as printed) */
const OFF_PAGE = new Set(["comment", "note", "metadata", "hidden", "deleted", "field", "link", "chart"]);
const PAGE_PX = 1400;
const usePages = (st) => st.layout === "page" && !!(st.job && st.job.page_view);
const pageSig = (st) => {
  const out = st.view === "preview" && st.output;
  return [st.pageVer, out || "src", out ? !!st.pg.out : !!st.pg.src, out ? st.pg.outErr : st.pg.srcErr, (out ? st.pg.out : st.pg.src) ? "" : (out ? st.pg.outBusy : st.pg.srcBusy)].join("|");
};

function boxItems(st) {
  const byText = new Map();
  for (const f of items(st)) {
    const s = st.segById[f.seg_id];
    if (s && OFF_PAGE.has(s.kind)) continue;
    const t = String(f.text).replace(/\s+/g, " ").trim();
    if (!byText.has(t)) byText.set(t, []);
    byText.get(t).push(f.id);
  }
  return [...byText].map(([text, ids]) => ({ text, ids }));
}

async function loadSourcePages(st) {
  const list = boxItems(st);
  const key = JSON.stringify(list);
  if (st.pg.srcBusy || key === st.pg.srcItems) return;
  st.pg.srcBusy = true;
  st.pg.srcErr = "";
  if (S === st) PR.render();
  try {
    const r = await PR.api(`api/jobs/${st.id}/pages`, PR.json("POST", { what: "source", items: list }));
    st.pg.src = r;
    st.pg.srcItems = key;
    st.pageVer++;
  } catch (e) {
    st.pg.srcErr = e.message;
    st.pg.srcItems = key;   // don't retry in a loop; switching back to this view retries
  }
  st.pg.srcBusy = false;
  if (S === st) PR.render();
}

function scheduleOutPages(st) {
  const opts = (st.job && st.job.options) || {};
  const body = Object.assign({ what: st.output, sanitize: opts.sanitize !== false }, payload(st));
  const key = JSON.stringify(body);
  if (key === st.pg.outKey || key === st.pg.outWant) return;
  st.pg.outWant = key;
  clearTimeout(st.pg.outTimer);
  st.pg.outTimer = setTimeout(async () => {
    const seq = ++st.pg.outSeq;
    st.pg.outBusy = true;
    st.pg.outErr = "";
    if (S === st) PR.render();
    try {
      const r = await PR.api(`api/jobs/${st.id}/pages`, PR.json("POST", body));
      if (seq !== st.pg.outSeq) return;
      st.pg.out = r;
      st.pg.outKey = key;
      st.pageVer++;
    } catch (e) {
      if (seq !== st.pg.outSeq) return;
      st.pg.outErr = e.message;
      st.pg.outWant = "";
    }
    st.pg.outBusy = false;
    if (S === st) PR.render();
  }, 500);
}

function pagesWanted(st) {
  if (!usePages(st)) return;
  if (st.view === "preview" && st.output) scheduleOutPages(st);
  else loadSourcePages(st);
}

function buildPages(st) {
  const node = st.pageNode || document.createElement("div");
  if (!st.pageNode) {
    node.className = "pgv";
    const hit = (ev) => {
      const b = ev.target.closest(".pbox");
      if (!b) return;
      const ids = b.dataset.ids.split(" ").filter((id) => id in st.on);
      const v = !ids.some((id) => st.on[id]);
      ids.forEach((id) => { st.on[id] = v; });
      PR.render();
    };
    node.addEventListener("click", hit);
    node.addEventListener("keydown", (ev) => { if (ev.key === "Enter" || ev.key === " ") { if (ev.target.closest(".pbox")) { ev.preventDefault(); hit(ev); } } });
    st.pageNode = node;
  }
  const out = st.view === "preview" && st.output;
  const d = out ? st.pg.out : st.pg.src;
  const busy = out ? st.pg.outBusy : st.pg.srcBusy;
  const err = out ? st.pg.outErr : st.pg.srcErr;
  if (!d) {
    node.innerHTML = err ? `<p class="pgv-msg">${esc(err)}</p>`
      : `<p class="pgv-msg"><span class="spin" aria-hidden="true"></span> ${busy ? "กำลังจัดหน้าเอกสาร…" : ""}</p>`;
    st.pageBuilt = pageSig(st);
    return;
  }
  const byPage = {};
  if (!out) for (const b of d.boxes || []) (byPage[b[0]] = byPage[b[0]] || []).push(b);
  let html = "";
  d.pages.forEach((p, i) => {
    const boxes = (byPage[i] || []).map(([, x0, y0, x1, y1, ids]) => {
      const l = ((x0 - 1) / p.w) * 100, t = ((y0 - 1) / p.h) * 100, w = ((x1 - x0 + 2) / p.w) * 100, h = ((y1 - y0 + 2) / p.h) * 100;
      const pos = `left:${l.toFixed(3)}%;top:${t.toFixed(3)}%;width:${w.toFixed(3)}%;height:${h.toFixed(3)}%;`;
      return `<span class="pbox" role="button" tabindex="0" data-ids="${esc(ids.join(" "))}" data-pos="${pos}" style="${pos}"></span>`;
    }).join("");
    html += `<div class="pg" data-page="${i}" style="aspect-ratio:${p.w}/${p.h}"><img loading="${i < 2 ? "eager" : "lazy"}" decoding="async" alt="หน้า ${i + 1}" `
      + `src="api/jobs/${esc(st.id)}/pages/${esc(d.key)}/${i}.png?w=${PAGE_PX}">${boxes}<span class="pg-no">${i + 1} / ${d.total}</span></div>`;
  });
  if (d.total > d.pages.length) html += `<p class="pgv-msg">แสดง ${d.pages.length} หน้าแรกจาก ${PR.num(d.total)} หน้า · หน้าที่เหลือดูได้ในมุมมองข้อความ</p>`;
  node.innerHTML = html;
  node.classList.toggle("dim", !!busy);
  st.pageBuilt = pageSig(st);
}

function paintBoxes(st) {
  const node = st.pageNode;
  if (!node) return;
  node.classList.toggle("dim", !!(st.view === "preview" && st.output ? st.pg.outBusy : false));
  const byId = {};
  for (const f of items(st)) byId[f.id] = f;
  node.querySelectorAll(".pbox").forEach((el) => {
    const fs = el.dataset.ids.split(" ").map((id) => byId[id]).filter(Boolean);
    if (!fs.length) { el.style.display = "none"; return; }
    const f = fs[0], t = PR.type(f.entity_type);
    const on = fs.some((x) => st.on[x.id]);
    const dim = st.typeFilter && st.typeFilter !== f.entity_type ? "opacity:.25;" : "";
    const css = (on
      ? `background:${t.bg};box-shadow:0 0 0 1.5px ${t.line}${fs.every((x) => isReview(x)) ? ";outline:1.5px dashed #F59E0B;outline-offset:2px" : ""}`
      : "background:transparent;outline:1.5px dashed #A1A1AA;outline-offset:0") + ";" + dim;
    if (el.__css !== css) { el.style.cssText = el.dataset.pos + css; el.__css = css; }
    el.title = `${t.label} · ${f.text}${fs.length > 1 ? ` · ${fs.length} จุด` : ""} · ${on ? "เลือกอยู่" : "ไม่ได้เลือก"} — คลิกเพื่อสลับ`;
    el.setAttribute("aria-pressed", on ? "true" : "false");
  });
}

function pageNote(st) {
  if (!usePages(st)) return "";
  if (st.view === "preview" && st.output) {
    return `หน้าตาไฟล์${st.output === "highlight" ? "ไฮไลต์" : `ที่ปิดบังแบบ${PR.modeLabel(st.mode)}`}จริง สร้างจากไฟล์ผลลัพธ์${st.pg.outBusy ? " · กำลังอัปเดต…" : ""}`;
  }
  const located = new Set();
  for (const b of (st.pg.src && st.pg.src.boxes) || []) b[5].forEach((id) => located.add(id));
  let off = 0, lost = 0;
  for (const f of items(st)) {
    const s = st.segById[f.seg_id];
    if (s && OFF_PAGE.has(s.kind)) off++;
    else if (st.pg.src && !located.has(f.id)) lost++;
  }
  const bits = ["คลิกกรอบเพื่อเลือก/ยกเลิก · ลากคลุมเพื่อเพิ่มจุดที่ตกหล่นได้ในมุมมอง “ข้อความ”"];
  if (off) bits.push(`${PR.num(off)} จุดอยู่ในคอมเมนต์ โน้ต ส่วนที่ซ่อน หรือข้อมูลไฟล์ ซึ่งไม่แสดงบนหน้า`);
  if (lost) bits.push(`${PR.num(lost)} จุดหาตำแหน่งบนหน้าไม่เจอ (ยังอยู่ในรายการด้านขวา)`);
  return bits.join(" · ");
}

/* ---------------------------------------------------------------- export */
async function exportNow(st) {
  if (!st.output || st.exporting || !st.loaded) return;
  const action = st.output;
  st.exporting = true;
  st.exportError = "";
  PR.render();
  try {
    const opts = (st.job && st.job.options) || {};
    const r = await PR.api(`api/jobs/${st.id}/export`, PR.json("POST",
      Object.assign({ action, sanitize: opts.sanitize !== false }, payload(st))));
    const mapping = r.mapping || null;
    delete r.mapping;
    PR.results[st.id] = PR.results[st.id] || {};
    PR.results[st.id][action] = { summary: r, mapping };
    st.job.outputs = Object.assign({}, st.job.outputs, { [action]: r });
    PR.hist.put(st.id, { exported: true, pii: r.count, mode: action === "redact" ? PR.modeLabel(st.mode) : "ไฮไลต์" });
    st.exporting = false;
    PR.go(`#/result/${st.id}/${action}`);
  } catch (e) {
    st.exporting = false;
    st.exportError = e.message;
    PR.render();
  }
}

/* ---------------------------------------------------------------- view */
function sw(on) { return { track: on ? "#18181B" : "#D4D4CF", knob: on ? "translateX(14px)" : "none" }; }

function scanVals(st, j) {
  const stage = j ? j.stage : "";
  const useLLM = j && j.options && j.options.use_llm && (j.options.profiles || []).length;
  const keys = ["rules"].concat(useLLM ? ["llm"] : [], ["merge"]);
  const curIdx = Math.max(0, keys.indexOf(stage));
  const labels = {
    rules: "กฎ checksum รายชื่อที่รู้จัก และ NER",
    llm: `AI ภายในองค์กร${useLLM ? ` (${j.options.profiles.length} ตัว)` : ""}`,
    merge: "รวมผล ตัดค่าซ้ำ และหาค่าเดียวกันทั้งไฟล์",
  };
  const steps = keys.map((k, i) => {
    const done = i < curIdx, cur = i === curIdx && j && j.status === "running";
    return { label: labels[k], done, fg: done ? "#3F3F46" : cur ? "#18181B" : "#A1A1AA",
      bg: done ? "#16A34A" : "#FFFFFF", border: done ? "#16A34A" : cur ? "#18181B" : "#D4D4CF" };
  });
  const det = stage === "llm" && j.total > 0;
  const secs = Math.floor((Date.now() - st.t0) / 1000);
  return {
    stageLabel: j && j.status === "queued" ? "รอคิวตรวจ" : `กำลัง${STAGE[stage] || "ตรวจ"}`,
    barCls: det ? "" : "indet", barPct: det ? `${Math.round((j.done / j.total) * 100)}%` : "35%",
    stageNote: det ? `อ่านแล้ว ${PR.num(j.done)} จาก ${PR.num(j.total)} ส่วน` : "ไฟล์ใหญ่หรือเปิดใช้ AI อาจใช้เวลาหลายนาที ปิดหน้านี้ได้ งานยังทำต่อ",
    elapsed: secs < 60 ? `${secs} วินาที` : `${Math.floor(secs / 60)} นาที ${secs % 60} วินาที`,
    steps,
    cancel: async () => {
      clearTimeout(st.timer);
      try { await PR.api(`api/jobs/${st.id}`, { method: "DELETE" }); } catch (_) { /* already gone */ }
      PR.hist.put(st.id, { expires: 0 });
      delete PR.review[st.id];
      PR.toast("ยกเลิกงานแล้ว");
      PR.go("#/upload");
    },
  };
}

function vals() {
  const st = S;
  const cfg = PR.cfg;
  const j = st.job;
  const hist = PR.hist.get(st.id) || {};
  const filename = (j && j.filename) || hist.name || "เอกสาร";
  const ext = (j && j.ext) || hist.ext || "";
  const failed = !!(st.error && (st.gone || !st.loaded)) || !!(j && j.status === "error");
  const ready = !failed && st.loaded;
  const scanning = !failed && !ready;
  const models = (j && j.models) || [];
  const base = {
    filename, extColor: PR.extColor(ext), extLabel: PR.extLabel(ext),
    scanning, failed, ready,
    statusDot: failed ? "#DC2626" : ready ? "#16A34A" : "#D97706",
    statusText: failed ? "ตรวจไม่สำเร็จ" : ready
      ? `ตรวจเสร็จใน ${j.seconds} วินาที · ${models.length ? models.map((m) => m.model.split("/").pop()).join(", ") : "ไม่ใช้ AI"}`
      : "กำลังตรวจ…",
    errorText: st.error || (j && j.error) || "",
  };
  if (scanning) return Object.assign(base, scanVals(st, j));
  if (!ready) return base;

  if (!st.docNode || st.builtVer !== st.docVer) buildDoc(st);
  const all = items(st);
  const groups = groupsOf(st);
  const onCount = all.filter((f) => st.on[f.id]).length;
  const out = st.output;
  const hext = j.highlight_ext || ext;
  const preview = st.view === "preview" && !!out;

  // groups list
  const shown = groups.filter((g) => (!st.onlyReview || g.review) && (!st.typeFilter || g.type === st.typeFilter));
  const groupRows = shown.map((g) => {
    const s = sw(g.on), t = PR.type(g.type);
    return {
      flag: g.review ? "#F59E0B" : "transparent", on: String(g.on), aria: `${g.on ? "ยกเลิก" : "เลือก"} ${g.text}`,
      track: s.track, knob: s.knob, deco: g.on ? "none" : "line-through", textFg: g.on ? "#18181B" : "#71717A",
      text: g.text, count: g.items.length, color: t.line, typeLabel: t.label, src: g.src, review: g.review,
      law: t.law ? `ฐานกฎหมาย / มาตรฐาน: ${t.law}` : "", sensitive: t.sensitive,
      manual: g.manual, score: g.score.toFixed(2), scorePct: `${Math.round(g.score * 100)}%`,
      scoreColor: g.review ? "#F59E0B" : "#2A78D6",
      toggle: () => { const v = !g.on; g.items.forEach((f) => { st.on[f.id] = v; }); },
      locate: () => { locate(st, g.items[0]); return false; },
      remove: () => {
        const ids = new Set(g.items.map((f) => f.id));
        st.manual = st.manual.filter((f) => !ids.has(f.id));
        ids.forEach((id) => delete st.on[id]);
        st.docVer++;
      },
    };
  });

  // types panel
  const tcount = {};
  all.forEach((f) => { tcount[f.entity_type] = (tcount[f.entity_type] || 0) + 1; });
  const tmax = Math.max(1, ...Object.values(tcount));
  const types = Object.entries(tcount).sort((a, b) => b[1] - a[1]).map(([k, n]) => {
    const t = PR.type(k);
    return { label: t.label, color: t.line, n: PR.num(n), pct: `${Math.round((n / tmax) * 100)}%`,
      on: String(st.typeFilter === k), bg: st.typeFilter === k ? "#F1F1EE" : "transparent",
      pick: () => { st.typeFilter = st.typeFilter === k ? "" : k; } };
  });
  const enabledTypes = ((j.options || {}).entities || [...PR.activeTypes()]).length;

  // parts chips (one per coarse location)
  const perGroup = {};
  all.forEach((f) => { const s = st.segById[f.seg_id]; const g = (s && s.group) || "เอกสาร"; perGroup[g] = (perGroup[g] || 0) + 1; });
  const parts = Object.keys(st.firstOfGroup || {}).map((g) => ({
    label: g, n: PR.num(perGroup[g] || 0),
    go: () => {
      const el = st.docNode && st.docNode.querySelector(`.doc-sec[data-sec="${st.firstOfGroup[g]}"]`);
      if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
      return false;
    },
  }));

  const secrets = all.filter((f) => f.entity_type === "SECRET").length;
  const warnings = j.warnings || [];
  const verb = out === "highlight" ? "จะไฮไลต์" : out === "redact" ? "จะปิดบัง" : "เลือกไว้";
  const segOff = { bg: "transparent", fg: "#52525B", sh: "none" }, segOn = { bg: "#FFFFFF", fg: "#18181B", sh: SEG_ON };
  const sv = st.view === "source" || !out ? segOn : segOff, pv = preview ? segOn : segOff;
  const p = st.pop;

  return Object.assign(base, {
    outs: [["highlight", "ไฮไลต์อย่างเดียว", "#FDE047"], ["redact", "ปิดบัง (Redact)", "#18181B"]].map(([k, label, swc]) => {
      const on = out === k;
      return { label, sw: swc, on: String(on), w: on ? "600" : "500", bg: on ? "#FFFFFF" : "transparent",
        fg: on ? "#18181B" : "#52525B", sh: on ? SEG_ON : "none",
        pick: () => { st.output = k; st.view = "preview"; st.pop = null; st.exportError = ""; } };
    }),
    isRedact: out === "redact", isHighlight: out === "highlight", askOut: !out,
    modes: Object.keys(cfg.modes).map((k, i) => {
      const on = st.mode === k;
      return { label: PR.modeLabel(k), tip: cfg.modes[k], on: String(on), sep: i ? "1px solid #E4E4DF" : "0",
        bg: on ? "#18181B" : "#FFFFFF", fg: on ? "#FFFFFF" : "#3F3F46", w: on ? "600" : "500",
        pick: () => { st.mode = k; st.view = "preview"; st.pop = null; } };
    }),
    hlBadge: `ได้ไฟล์ ${hext}${hext === ext ? " เดิม" : ""} ไฮไลต์สีเหลืองทุกจุดที่เลือก + รายงาน .csv`,
    kpis: [
      { label: "พบทั้งหมด", value: PR.num(all.length), sub: `จุด · ${PR.num(groups.length)} ค่า` },
      { label: "ควรตรวจ", value: PR.num(groups.filter((g) => g.review).length), sub: "ค่าที่ระบบไม่มั่นใจ" },
      { label: out === "highlight" ? "จะไฮไลต์" : out === "redact" ? "จะปิดบัง" : "เลือกไว้", value: PR.num(onCount), sub: "จุด" },
      { label: "ประเภท", value: PR.num(Object.keys(tcount).length), sub: `จาก ${enabledTypes} ประเภท` },
    ],
    showNote: secrets > 0 || warnings.length > 0, hasSecret: secrets > 0, warnings,
    bannerHead: out === "highlight" ? "ไฟล์ไฮไลต์ยังมีข้อมูลจริงทุกจุด" : `พบรหัสผ่าน ${secrets} จุด`,
    bannerBody: out === "highlight" ? `รวมรหัสผ่าน ${secrets} จุด ห้ามส่งออกนอกทีม` : "ลบทิ้งเสมอในทุกโหมด และไม่ถูกบันทึกในไฟล์กุญแจ",
    parts: usePages(st) ? [] : parts,
    pageOk: !!j.page_view, pageNote: pageNote(st),
    layouts: [["page", j.kind === "pptx" ? "สไลด์" : "หน้าเอกสาร"], ["text", "ข้อความ"]].map(([k, label]) => {
      const on = st.layout === k, c = on ? segOn : segOff;
      return { label, on: String(on), bg: c.bg, fg: c.fg, sh: c.sh, pick: () => { st.layout = k; st.pop = null; } };
    }),
    isSource: String(!preview), isPreview: String(preview),
    srcBg: sv.bg, srcFg: sv.fg, srcSh: sv.sh, pvBg: pv.bg, pvFg: pv.fg, pvSh: pv.sh,
    pvDis: out ? "" : "disabled", pvTip: out ? "" : "เลือกผลลัพธ์ด้านบนก่อน",
    viewSource: () => { st.view = "source"; },
    viewPreview: () => { if (!out) return false; st.view = "preview"; st.pop = null; },
    previewNote: preview && !usePages(st) ? (out === "highlight"
      ? "ตัวอย่างไฟล์ไฮไลต์ · สีเหลือง = จุดที่เลือก · กลับไปที่ “ต้นฉบับ” เพื่อเพิ่มรายการเอง"
      : `ตัวอย่างหลังปิดบังแบบ${PR.modeLabel(st.mode)}${st.pvLoading ? " · กำลังโหลด…" : ""} · กลับไปที่ “ต้นฉบับ” เพื่อเพิ่มรายการเอง`) : "",
    pop: p && {
      top: p.top, left: p.left, text: p.text.length > 60 ? `${p.text.slice(0, 60)}…` : p.text, color: PR.type(p.type).line,
      types: Object.keys(cfg.entity_types).map((k) => ({ value: k, label: PR.type(k).label, sel: k === p.type ? "selected" : "" })),
      setType: (ev) => { p.type = ev.target.value; },
      toggleAll: (ev) => { p.all = ev.target.checked; },
      allChecked: p.all ? "checked" : "", occ: PR.num(Math.max(1, p.occ.length)),
      add: () => { addManual(st); },
      cancel: () => { st.pop = null; },
    },
    listSub: `${PR.num(groups.length)} ค่า · จัดกลุ่มค่าที่ซ้ำกัน`,
    onlyReview: String(st.onlyReview), toggleReview: () => { st.onlyReview = !st.onlyReview; },
    rvBg: st.onlyReview ? "#FFFBEB" : "#FFFFFF", rvTrack: st.onlyReview ? "#18181B" : "#D4D4CF",
    rvKnob: st.onlyReview ? "translateX(12px)" : "none",
    typeFilterOn: !!st.typeFilter, typeFilterLabel: st.typeFilter ? PR.type(st.typeFilter).label : "",
    clearType: () => { st.typeFilter = ""; },
    groups: groupRows, noGroups: !groupRows.length,
    emptyText: groups.length ? "ไม่มีรายการที่ตรงกับตัวกรอง" : "ไม่พบข้อมูลส่วนบุคคลในไฟล์นี้ — ลากคลุมข้อความในเอกสารเพื่อเพิ่มเองได้",
    types,
    verb, acceptedN: PR.num(onCount), totalN: PR.num(all.length),
    outDesc: out === "highlight" ? `ไฟล์ไฮไลต์ ${hext}` : out === "redact" ? `${PR.modeLabel(st.mode)} · ไฟล์ ${ext}` : "ยังไม่ได้เลือกผลลัพธ์",
    outNote: out === "highlight" ? "ไฟล์ไฮไลต์ยังมีข้อมูลจริง ใช้ภายในทีมเท่านั้น · กลับมาปิดบังต่อได้โดยไม่ต้องตรวจใหม่"
      : out === "redact" ? `ตรวจซ้ำหลังปิดบังให้อัตโนมัติ · ได้ไฟล์ ${ext} รูปแบบเดิม${st.mode === "token" ? " + ไฟล์กุญแจ .piimap" : ""}`
      : "เลือก “ไฮไลต์อย่างเดียว” หรือ “ปิดบัง” ด้านบนก่อนสร้างไฟล์",
    exportError: st.exportError, exporting: st.exporting,
    ctaLabel: st.exporting ? "กำลังสร้างไฟล์…" : out === "highlight" ? "สร้างไฟล์ไฮไลต์" : out === "redact" ? "ปิดบังและสร้างไฟล์" : "สร้างไฟล์",
    ctaDis: !out || st.exporting ? "disabled" : "",
    exportNow: () => { exportNow(st); return false; },
    back: () => { PR.go("#/upload"); return false; },
  });
}

PR.views.review = {
  tpl: "review",
  title: () => "ตรวจทีละจุด",
  enter([id]) {
    S = stateFor(id);
    S.pop = null;
    if (keyHandler) window.removeEventListener("keydown", keyHandler);
    keyHandler = (ev) => { if (ev.key === "Escape" && S && S.pop) { S.pop = null; PR.render(); } };
    window.addEventListener("keydown", keyHandler);
    poll(S);
  },
  leave() {
    if (S) { clearTimeout(S.timer); clearTimeout(S.pvTimer); clearTimeout(S.pg.outTimer); }
    if (keyHandler) window.removeEventListener("keydown", keyHandler);
    keyHandler = null;
  },
  vals,
  slots: {
    doc: () => {
      if (!S || !S.loaded) return null;
      if (usePages(S)) {
        if (!S.pageNode || S.pageBuilt !== pageSig(S)) buildPages(S);
        return S.pageNode;
      }
      if (!S.docNode || S.builtVer !== S.docVer) buildDoc(S);
      return S.docNode;
    },
  },
  after(root) {
    const st = S;
    if (!st || !st.loaded) return;
    const slot = root.querySelector('[data-slot="doc"]');
    if (!slot) return;
    if (usePages(st)) {
      pagesWanted(st);
      if (!st.pageNode || st.pageBuilt !== pageSig(st)) buildPages(st);
      if (slot.firstChild !== st.pageNode) slot.replaceChildren(st.pageNode);
      slot.classList.add("pg-slot");
      paintBoxes(st);
      return;
    }
    slot.classList.remove("pg-slot");
    if (!st.docNode || st.builtVer !== st.docVer) buildDoc(st);
    if (slot.firstChild !== st.docNode) slot.replaceChildren(st.docNode);
    paintMarks(st);
    schedulePreview(st);
    if (st.popFocus) {
      st.popFocus = false;
      const sel = document.getElementById("poptype");
      if (sel) sel.focus({ preventScroll: true });
    }
  },
};
PR.reviewState = stateFor;
})();
