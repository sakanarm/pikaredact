/* Analytics (usage statistics from /api/stats) and Leaderboard (offline evaluation from /api/leaderboard).
   Only counts are stored on the server; no value that was found is ever kept. */
(() => {
"use strict";
const PR = window.PR;

const CAT = ["#2A78D6", "#EB6834", "#1BAF7A", "#EDA100", "#E87BA4", "#7C5CD6", "#4B9C9C"];
const RAMP = ["#CDE2FB", "#B7D3F6", "#9EC5F4", "#86B6EF", "#6DA7EC", "#5598E7", "#3987E5", "#2A78D6", "#256ABF", "#1C5CAB", "#184F95", "#104281", "#0D366B"];
/* colour follows the output, never its rank */
const OUTS = [["redact:token", "ปิดบัง · แทนด้วยรหัส"], ["highlight", "ไฮไลต์อย่างเดียว"], ["redact:remove", "ปิดบัง · ลบทิ้ง"],
  ["redact:fake", "ปิดบัง · ข้อมูลปลอม"], ["redact:mask", "ปิดบัง · ปิดบางส่วน"]];
const LAYERS = ["rule", "list", "ner", "table", "llm", "propagate", "manual"];
const EXT_KIND = { DOCX: "Word", XLSX: "Excel", PPTX: "PowerPoint", PDF: "PDF", TXT: "ข้อความ", MD: "Markdown", CSV: "CSV" };
/* typical false positives per type: where to look when a type is rejected often */
const FP_WHY = {
  ORGANIZATION: "ชื่อ product ชื่อ cloud region ชื่อระบบภายใน", OTHER_ID: "ชื่อ service account ชื่อตาราง รหัสระบบ",
  IP_ADDRESS: "IP ตัวอย่างในเอกสารออกแบบ เช่น 10.0.0.0/16", PERSON: "ชื่อเล่นหรือคำทั่วไปที่ดูเหมือนชื่อคน",
  ADDRESS: "ชื่อสถานที่หรือชื่อตึกทั่วไป", PHONE: "เลขเวอร์ชัน เลขพอร์ต หรือรหัสสินค้า", DATE_OF_BIRTH: "วันที่ทั่วไปในเอกสาร",
  BANK_ACCOUNT: "เลขอ้างอิงที่มีรูปแบบคล้ายบัญชี", PASSPORT: "รหัสสินค้าหรือรหัสเอกสาร", EMAIL: "อีเมลกลางของระบบหรือตัวอย่าง",
  TH_LICENSE_PLATE: "รหัสที่มีตัวอักษรไทยปนตัวเลข", SECRET: "ค่าตัวอย่างใน config", CREDIT_CARD: "เลขยาวที่ผ่าน Luhn โดยบังเอิญ",
  TH_ID_CARD: "เลข 13 หลักที่ผ่าน checksum โดยบังเอิญ",
};
const CRITICAL_DEFAULT = ["TH_ID_CARD", "SECRET", "CREDIT_CARD", "BANK_ACCOUNT", "PASSPORT"];

const S = { tab: "analytics", days: 30, ext: "", model: "", stats: null, busy: false, error: "", seq: 0, board: null, boardBusy: false };
const fmt = (n) => PR.num(Math.round(n || 0));
const pct1 = (v) => (v == null ? "–" : `${(v * 100).toFixed(1)}%`);

async function loadStats() {
  const my = ++S.seq;
  S.busy = true;
  S.error = "";
  PR.render();
  try {
    const q = new URLSearchParams({ days: S.days, ext: S.ext, model: S.model });
    const r = await PR.api(`api/stats?${q}`);
    if (my !== S.seq) return;
    S.stats = r;
  } catch (e) {
    if (my !== S.seq) return;
    S.error = e.message;
  }
  S.busy = false;
  PR.render();
}
async function loadBoard() {
  S.boardBusy = true;
  S.error = "";
  PR.render();
  try { S.board = await PR.api("api/leaderboard"); } catch (e) { S.error = e.message; }
  S.boardBusy = false;
  PR.render();
}

/* ---------------------------------------------------------------- overview */
function niceStep(max) {
  const raw = Math.max(1, max) / 3;
  const p = Math.pow(10, Math.floor(Math.log10(raw)));
  const m = raw / p;
  return (m <= 1 ? 1 : m <= 2 ? 2 : m <= 5 ? 5 : 10) * p;
}

function delta(cur, prev, kind, lowerBetter) {
  if (cur == null || prev == null) return { delta: "", note: "ยังไม่มีข้อมูลช่วงก่อนหน้า", arrow: false };
  let d, text;
  if (kind === "pct") {
    if (!prev) return { delta: "", note: "ยังไม่มีข้อมูลช่วงก่อนหน้า", arrow: false };
    d = (cur - prev) / prev;
    text = `${Math.abs(d * 100).toFixed(0)}%`;
  } else {
    d = cur - prev;
    text = `${Math.abs(d * 100).toFixed(1)} จุด`;
  }
  if (Math.abs(d) < 1e-9) return { delta: "เท่าเดิม", dFg: "#52525B", arrow: false };
  const good = lowerBetter ? d < 0 : d > 0;
  return { delta: text, dFg: good ? "#006300" : "#B91C1C", rot: d > 0 ? "none" : "rotate(180deg)", arrow: true };
}

function overviewVals() {
  const st = S.stats;
  if (!st) return { loaded: false, firstLoad: S.busy && !S.error };
  const cur = st.summary || {}, prev = st.previous || {};
  const hadPrev = (prev.docs || 0) > 0;
  const avg = cur.docs ? (cur.findings / cur.docs) : 0;
  const kpis = [
    Object.assign({ label: "เอกสารที่ตรวจ", value: fmt(cur.docs) }, hadPrev ? delta(cur.docs, prev.docs, "pct") : delta(null), { note: hadPrev ? `เทียบ ${st.days} วันก่อน` : "ยังไม่มีข้อมูลช่วงก่อนหน้า" }),
    Object.assign({ label: "ข้อมูลส่วนบุคคลที่พบ", value: fmt(cur.findings) }, hadPrev ? delta(cur.findings, prev.findings, "pct") : {}, { note: cur.docs ? `เฉลี่ย ${avg.toFixed(avg < 10 ? 1 : 0)} จุดต่อเอกสาร` : "ยังไม่มีงาน" }),
    Object.assign({ label: "ยอมรับตามระบบ", value: pct1(cur.accept_rate) }, delta(cur.accept_rate, prev.accept_rate, "pt"), { note: "จุดที่คนไม่ยกเลิก" }),
    Object.assign({ label: "คนต้องเพิ่มเอง", value: pct1(cur.manual_rate) }, delta(cur.manual_rate, prev.manual_rate, "pt", true), { note: "ระบบพลาด ยิ่งต่ำยิ่งดี" }),
  ].map((k) => Object.assign({ delta: "", arrow: false, dFg: "#52525B", rot: "none" }, k));

  const hasData = (st.scans || 0) > 0;
  const out = { loaded: true, firstLoad: false, kpis, hasData, noData: !hasData, days: st.days };
  if (!hasData) return out;

  /* findings per day: one series, line + area, hover crosshair */
  const data = st.per_day.map((d) => d.findings);
  const n = data.length;
  const step = niceStep(Math.max(...data));
  const ymax = step * 3;
  const xs = (i) => (n > 1 ? (i / (n - 1)) * 300 : 150), ys = (v) => 100 - (v / ymax) * 100;
  let line = "";
  data.forEach((v, i) => { line += `${i ? " L" : "M"}${xs(i).toFixed(2)} ${ys(v).toFixed(2)}`; });
  const dates = st.per_day.map((d) => { const [y, m, dd] = d.date.split("-").map(Number); return PR.thDate(new Date(y, m - 1, dd)); });
  const w = n > 1 ? 100 / (n - 1) : 100;
  const pts = st.per_day.map((d, i) => {
    const edgeL = i < 3, edgeR = i > n - 4;
    return { left: `${i * w - w / 2}%`, w: `${w}%`, bottom: `${((d.findings / ymax) * 100).toFixed(2)}%`, v: fmt(d.findings), docs: fmt(d.docs), date: dates[i],
      tl: edgeR ? "auto" : "50%", tr: edgeR ? "50%" : "auto", tt: edgeL || edgeR ? "none" : "translateX(-50%)" };
  });
  const grid = [0, 1, 2, 3].map((k) => ({ pos: `${(k / 3) * 100}%`, label: fmt(step * k), line: k === 0 ? "#C3C2B7" : "#E1E0D9" }));
  const xi = n <= 8 ? [...Array(n).keys()] : [0, 1, 2, 3, 4].map((k) => Math.round((k * (n - 1)) / 4));
  /* end labels hug the plot edges so they never spill outside the card */
  const xlabels = xi.map((i, k) => ({ f: (n > 1 ? i / (n - 1) : 0.5).toFixed(4), label: dates[i],
    tx: n > 1 && k === 0 ? "0" : n > 1 && k === xi.length - 1 ? "-100%" : "-50%" }));

  /* top types */
  const byType = Object.entries(st.by_type || {}).sort((a, b) => b[1] - a[1]);
  const tot = byType.reduce((a, b) => a + b[1], 0) || 1;
  const topMax = byType.length ? byType[0][1] : 1;
  const topTypes = byType.slice(0, 8).map(([t, c]) => {
    const found = (st.found_by_type || {})[t] || 0, rej = (st.rejected_by_type || {})[t] || 0;
    return { label: PR.type(t).label, n: fmt(c), pct: `${((c / topMax) * 100).toFixed(1)}%`, share: `${((c / tot) * 100).toFixed(1)}%`,
      rej: found ? pct1(rej / found) : "–" };
  });

  /* layers: what people confirmed, by the layer that found it first */
  const acc = Object.assign({}, st.accepted_by_layer || {});
  if (st.manual) acc.manual = st.manual;
  const lsum = LAYERS.reduce((a, l) => a + (acc[l] || 0), 0);
  const lrows = LAYERS.map((l, i) => ({ l, i, n: acc[l] || 0 })).filter((x) => x.n > 0);
  const layers = lrows.map((x, k) => {
    const share = (x.n / lsum) * 100;
    const found = (st.found_by_layer || {})[x.l];
    return { label: (PR.cfg.layers || {})[x.l] || x.l, share: `${share.toFixed(share < 10 ? 1 : 0)}%`, pct: `calc(${share}% - 2px)`,
      acc: x.l === "manual" ? "100%" : found ? pct1(x.n / found) : "–", color: CAT[x.i % CAT.length],
      rad: lrows.length === 1 ? "4px" : k === 0 ? "4px 0 0 4px" : k === lrows.length - 1 ? "0 4px 4px 0" : "0" };
  });

  /* outputs */
  const outs = OUTS.map(([k, label], i) => ({ k, label, n: (st.outputs || {})[k] || 0, color: CAT[i] })).filter((o) => o.n > 0).sort((a, b) => b.n - a.n);
  const omax = outs.length ? outs[0].n : 1;

  /* per file type */
  const docTypes = Object.entries(st.per_ext || {}).sort((a, b) => b[1].docs - a[1].docs).map(([k, d]) => ({
    k, kind: EXT_KIND[k] || "", color: PR.extColor(k), n: fmt(d.docs), per: d.docs ? (d.findings / d.docs).toFixed(1) : "–",
    acc: d.found ? pct1(d.acc / d.found) : "–", man: d.docs ? (d.manual / d.docs).toFixed(1) : "–",
  }));

  /* most rejected types */
  const fps = Object.entries(st.found_by_type || {}).map(([t, f]) => ({ t, f, r: (st.rejected_by_type || {})[t] || 0 }))
    .filter((x) => x.r > 0 && x.f >= 3).map((x) => Object.assign(x, { rate: x.r / x.f })).sort((a, b) => b.rate - a.rate).slice(0, 5)
    .map((x) => ({ label: PR.type(x.t).label, why: `${FP_WHY[x.t] || "ตรวจคำที่ถูกยกเลิกบ่อยแล้วเพิ่มเป็นคำยกเว้น"} · ยกเลิก ${fmt(x.r)} จาก ${fmt(x.f)}`,
      rate: pct1(x.rate), lvl: x.rate >= 0.1 ? "สูง" : x.rate >= 0.03 ? "ปานกลาง" : "ต่ำ", fg: "#3F3F46",
      ic: x.rate >= 0.1 ? "#EC835A" : x.rate >= 0.03 ? "#FAB219" : "#A1A1AA" }));
  const exported = (st.exports || 0) > 0;

  return Object.assign(out, {
    rangeText: `${dates[0]} – ${dates[n - 1]}`, chartTotal: `${fmt(data.reduce((a, b) => a + b, 0))} จุด`,
    areaD: `${line} L${xs(n - 1).toFixed(2)} 100 L${xs(0).toFixed(2)} 100 Z`, lineD: line, pts, grid, xlabels, topTypes,
    layers, hasLayers: layers.length > 0, noLayers: !layers.length, layerAria: layers.map((l) => `${l.label} ${l.share}`).join(", "),
    outputs: outs.map((o) => ({ label: o.label, n: fmt(o.n), pct: `${((o.n / omax) * 100).toFixed(1)}%`, color: o.color })), noOutputs: !outs.length,
    docTypes, fps, noFps: !fps.length,
    noFpsText: exported ? "ยังไม่มีประเภทที่ถูกยกเลิกบ่อย" : "จะแสดงหลังมีงานที่ตรวจทานและดาวน์โหลดผลลัพธ์แล้ว",
  });
}

function filterVals() {
  const f = (S.stats && S.stats.filters) || { exts: [], models: [] };
  const exts = [...new Set([...(f.exts || []), ...(S.ext ? [S.ext] : [])])];
  const models = [...new Set([...(f.models || []), ...(S.model && S.model !== "none" ? [S.model] : [])])];
  const opt = (v, label, cur) => ({ v, label, sel: String(v) === String(cur) ? "selected" : "" });
  const set = (k) => (ev) => { S[k] = k === "days" ? Number(ev.target.value) : ev.target.value; loadStats(); return false; };
  return [
    { aria: "ช่วงเวลา", set: set("days"), opts: [7, 30, 90, 365].map((d) => opt(d, d === 365 ? "1 ปีล่าสุด" : `${d} วันล่าสุด`, S.days)) },
    { aria: "ประเภทไฟล์", set: set("ext"), opts: [opt("", "ทุกประเภทไฟล์", S.ext), ...exts.map((e) => opt(e, PR.extLabel(e), S.ext))] },
    { aria: "Connector / model", set: set("model"), opts: [opt("", "ทุก connector", S.model), opt("none", "ไม่ใช้ AI", S.model), ...models.map((m) => opt(m, m, S.model))] },
  ];
}

/* ---------------------------------------------------------------- leaderboard */
const MISS = {
  good: { mBg: "#F0FDF4", mFg: "#166534", mDot: "#0CA30C" },
  warn: { mBg: "#FFFBEB", mFg: "#92400E", mDot: "#FAB219" },
  crit: { mBg: "#FEF2F2", mFg: "#991B1B", mDot: "#D03B3B" },
};
const num3 = (v) => (v == null || isNaN(v) ? "–" : Number(v).toFixed(3));
function boardVals() {
  const B = S.board;
  const rows = (B && B.rows) || [];
  if (!rows.length) return { boardEmpty: !S.boardBusy, boardHas: false, firstLoad: S.boardBusy };
  const sorted = rows.slice().sort((a, b) => (b.recall || 0) - (a.recall || 0) || (b.f2 || 0) - (a.f2 || 0));
  const ds = B.dataset || {};
  const board = sorted.map((r, i) => {
    const miss = r.critical_missed != null ? r.critical_missed : null;
    const lvl = miss == null ? MISS.warn : miss === 0 ? MISS.good : miss < 5 ? MISS.warn : MISS.crit;
    const sec = r.seconds_per_doc != null ? r.seconds_per_doc : r.seconds;
    return Object.assign({ rank: String(i + 1), name: r.name || "–", sub: r.sub || r.connector || "", r: num3(r.recall),
      rPct: `${((r.recall || 0) * 100).toFixed(1)}%`, p: num3(r.precision), f2: num3(r.f2),
      miss: miss == null ? "–" : miss === 0 ? "ไม่พลาด" : `พลาด ${miss}`, lat: sec == null ? "–" : `${sec < 10 ? Number(sec).toFixed(1) : Math.round(sec)} s`,
      bg: r.current ? "#FAFAF8" : "transparent" }, lvl);
  });
  const cols = (B.columns && B.columns.length ? B.columns : Object.keys((sorted[0] && sorted[0].by_type) || {}));
  const cell = (r, t) => {
    const v = (r.by_type || {})[t];
    if (v == null) return { v: "–", bg: "#F4F4F1", fg: "#898781", tip: `${PR.type(t).label} · ไม่มีในชุดทดสอบ` };
    const k = Math.round(Math.max(0, Math.min(1, (v - 0.4) / 0.6)) * (RAMP.length - 1));
    const tot = (r.total_by_type || ds.total_by_type || {})[t];
    const missed = (r.missed_by_type || {})[t] != null ? r.missed_by_type[t] : tot != null ? Math.round(tot * (1 - v)) : null;
    return { v: Number(v).toFixed(2), bg: RAMP[k], fg: k >= 7 ? "#FFFFFF" : "#0B0B0B",
      tip: `${PR.type(t).label} · ${missed == null ? `recall ${Number(v).toFixed(2)}` : `พลาด ${fmt(missed)}${tot != null ? ` จาก ${fmt(tot)}` : ""} จุด`}` };
  };
  return {
    boardEmpty: false, boardHas: true, firstLoad: false,
    ds: { name: ds.name || "ชุดทดสอบสังเคราะห์", docs: ds.docs != null ? fmt(ds.docs) : "–", spans: ds.spans != null ? fmt(ds.spans) : "–",
      kinds: Array.isArray(ds.doc_types) ? ds.doc_types.join(" ") : ds.doc_types || "" },
    board, hasHm: cols.length > 0, hmN: String(cols.length), hmMin: `${240 + cols.length * 70}px`,
    hmCols: cols.map((t) => ({ label: PR.type(t).label })), hmRows: sorted.map((r) => ({ name: r.name, cells: cols.map((t) => cell(r, t)) })),
    boardNote: `ผลจาก var/leaderboard.json${B.generated ? ` · รันเมื่อ ${new Date(B.generated * 1000).toLocaleString("th-TH")}` : ""} · พลาดจุดร้ายแรง = ${(B.critical || CRITICAL_DEFAULT).map((t) => PR.type(t).label).join(" ")}`,
  };
}

/* ---------------------------------------------------------------- export */
const csvCell = (v) => { const s = v == null ? "" : String(v); return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };
const csvRows = (rows) => rows.map((r) => r.map(csvCell).join(",")).join("\r\n");
function exportCsv() {
  const stamp = new Date().toISOString().slice(0, 10);
  if (S.tab === "leaderboard") {
    const rows = ((S.board && S.board.rows) || []);
    const cols = (S.board.columns && S.board.columns.length ? S.board.columns : Object.keys((rows[0] && rows[0].by_type) || {}));
    const out = [["name", "sub", "recall", "precision", "f2", "critical_missed", "seconds_per_doc", ...cols.map((c) => `recall_${c}`)],
      ...rows.map((r) => [r.name, r.sub || r.connector || "", r.recall, r.precision, r.f2, r.critical_missed, r.seconds_per_doc != null ? r.seconds_per_doc : r.seconds,
        ...cols.map((c) => (r.by_type || {})[c])])];
    PR.save(new Blob(["﻿" + csvRows(out)], { type: "text/csv" }), `pikaredact-leaderboard-${stamp}.csv`);
    return;
  }
  const st = S.stats;
  const out = [["section", "key", "label", "value", "value2", "value3"],
    ["filter", "days", "", st.days, "", ""], ["filter", "ext", "", S.ext || "all", "", ""], ["filter", "model", "", S.model || "all", "", ""],
    ["summary", "docs", "เอกสารที่ตรวจ", st.summary.docs, "", ""], ["summary", "findings", "จุดที่พบ", st.summary.findings, "", ""],
    ["summary", "accept_rate", "ยอมรับตามระบบ", st.summary.accept_rate, "", ""], ["summary", "manual_rate", "คนต้องเพิ่มเอง", st.summary.manual_rate, "", ""],
    ...st.per_day.map((d) => ["per_day", d.date, "", d.findings, d.docs, ""]),
    ...Object.entries(st.by_type || {}).map(([t, c]) => ["by_type", t, PR.type(t).label, c, (st.found_by_type || {})[t] || 0, (st.rejected_by_type || {})[t] || 0]),
    ...Object.entries(st.accepted_by_layer || {}).map(([l, c]) => ["accepted_by_layer", l, (PR.cfg.layers || {})[l] || l, c, (st.found_by_layer || {})[l] || 0, ""]),
    ...Object.entries(st.outputs || {}).map(([k, c]) => ["outputs", k, "", c, "", ""]),
    ...Object.entries(st.per_ext || {}).map(([k, d]) => ["per_ext", k, "", d.docs, d.findings, d.found ? d.acc / d.found : ""]),
  ];
  PR.save(new Blob(["﻿" + csvRows(out)], { type: "text/csv" }), `pikaredact-analytics-${st.days}d-${stamp}.csv`);
}

/* ---------------------------------------------------------------- view */
function vals() {
  const ov = S.tab === "analytics";
  const tabs = [["analytics", "ภาพรวม"], ["leaderboard", "Leaderboard"]].map((t) => {
    const on = t[0] === S.tab;
    return { label: t[1], on: String(on), pick: () => { PR.go(`#/${t[0]}`); return false; },
      w: on ? "600" : "500", fg: on ? "#18181B" : "#71717A", line: on ? "#18181B" : "transparent" };
  });
  const canExport = ov ? !!S.stats : !!(S.board && S.board.rows && S.board.rows.length);
  const base = {
    tabs, isOverview: ov, isBoard: !ov, crumb: ov ? "Analytics" : "Leaderboard", h1: ov ? "Analytics" : "Leaderboard",
    lead: ov ? "สถิติรวมจากทุกงาน ใช้ดูว่าระบบพลาดตรงไหนและควรปรับอะไร"
      : "เทียบชุดการตรวจแต่ละแบบบนชุดทดสอบเดียวกัน เลือก connector ที่จับได้ครบที่สุดในเวลาที่รับได้",
    busy: S.busy, error: S.error, retry: () => { if (ov) loadStats(); else loadBoard(); return false; },
    exportCsv: () => { if (canExport) exportCsv(); return false; }, exportDis: canExport ? "" : "disabled",
    filters: filterVals(),
  };
  return Object.assign(base, ov ? overviewVals() : boardVals());
}

PR.views.analytics = {
  tpl: "analytics",
  title: ([tab]) => (tab === "leaderboard" ? "Leaderboard" : "Analytics"),
  enter([tab]) {
    S.tab = tab;
    if (tab === "leaderboard") loadBoard();
    else loadStats();
  },
  vals,
};
})();
