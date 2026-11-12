/* Upload screen: pick files or paste text, choose detection layers + AI connectors, start one job per file. */
(() => {
"use strict";
const PR = window.PR;
const S = { init: false, tab: "file", files: [], text: "", drag: false, busy: false, error: "" };
const KINDS = { ".docx": "docx", ".xlsx": "xlsx", ".pptx": "pptx", ".pdf": "pdf", ".csv": "csv" };
const kindOf = (ext) => KINDS[ext] || "text";
const LAYERS = [
  { key: "rules", label: "กฎและ checksum", desc: "เลขบัตร บัตรเครดิต เบอร์โทร อีเมล บัญชี IP รหัสผ่าน", locked: true },
  { key: "ner", label: "ชื่อและที่อยู่ภาษาไทย", desc: "ตัดคำด้วย PyThaiNLP + คำนำหน้าชื่อ + หัวตาราง" },
  { key: "list", label: "รายชื่อที่รู้จัก", desc: "พนักงาน ลูกค้า และคำยกเว้นจากหน้าตั้งค่า" },
  { key: "ai", label: "AI ภายในองค์กร", desc: "อ่านบริบท จับชื่อเล่นและสิ่งที่กฎจับไม่ได้" },
];
const ST = {
  ok: { bg: "#DCFCE7", fg: "#166534", dot: "#16A34A" },
  wait: { bg: "#FEF3C7", fg: "#92400E", dot: "#D97706" },
  gone: { bg: "#F4F4F1", fg: "#52525B", dot: "#A1A1AA" },
};

const modelOf = (p) => ((PR.cfg.settings.models || {})[p.name] || p.model || "");

function init() {
  const st = PR.cfg.settings || {};
  const names = (PR.cfg.profiles || []).map((p) => p.name);
  const def = st.default_connector;
  const want = def && def !== "none" ? [def] : (st.profiles || []);
  S.picked = want.filter((n) => names.includes(n));
  S.ai = def !== "none" && S.picked.length > 0 && (!!st.use_llm || !!def);
  S.layers = { ner: !st.layers || st.layers.ner !== false, list: !st.layers || st.layers.list !== false };
  S.combine = st.combine === "vote" ? "vote" : "union";
  S.minAgree = st.min_agree || 2;
  S.sanitize = st.sanitize !== false;
  S.packs = [...(st.packs_on || [])];
  S.init = true;
}
/* settings changed: pick up the new defaults next time the upload page opens */
PR.resetUploadDefaults = () => { S.init = false; };

function addFiles(list) {
  const cfg = PR.cfg;
  const max = cfg.max_upload_mb * 1024 * 1024;
  for (const file of list || []) {
    const dot = file.name.lastIndexOf(".");
    const ext = dot >= 0 ? file.name.slice(dot).toLowerCase() : "";
    if (S.files.some((f) => f.name === file.name && f.size === file.size)) continue;
    let err = "";
    if (!cfg.supported.includes(ext)) err = "ไม่รองรับไฟล์ประเภทนี้";
    else if (file.size > max) err = `ใหญ่เกิน ${cfg.max_upload_mb} MB`;
    else if (!file.size) err = "ไฟล์ว่าง";
    S.files.push({ file, name: file.name, ext, size: file.size, err });
  }
  S.tab = "file";
  S.error = "";
}

function chosenProfiles() {
  return (PR.cfg.profiles || []).filter((p) => S.picked.includes(p.name));
}
function useAI() { return S.ai && S.picked.length > 0; }

function options() {
  const profs = useAI() ? chosenProfiles() : [];
  const agree = Math.max(2, Math.min(S.minAgree, profs.length || 2));
  return {
    use_llm: profs.length > 0,
    profiles: profs.map((p) => ({ name: p.name, model: modelOf(p) })),
    combine: profs.length > 1 ? S.combine : "union",
    min_agree: agree,
    layers: { ner: S.layers.ner, list: S.layers.list },
    sanitize: S.sanitize,
    packs: S.packs,
  };
}

/* entity types this job will look for: packs picked here, minus the per-type switches in settings */
function packTypes() {
  const off = new Set((PR.cfg.settings || {}).types_off || []);
  const on = new Set(["SECRET"]);
  for (const p of PR.cfg.packs || []) if (p.locked || S.packs.includes(p.id)) p.types.forEach((t) => on.add(t));
  return [...on].filter((t) => t === "SECRET" || !off.has(t));
}

async function start() {
  if (S.busy) return false;
  S.error = "";
  const missing = useAI() ? chosenProfiles().filter((p) => !modelOf(p)) : [];
  if (missing.length) { S.error = `ยังไม่ได้เลือก model ของ ${missing.map((p) => p.name).join(", ")} — เลือกได้ที่หน้า Model connector`; return; }
  const isFile = S.tab === "file";
  const items = isFile ? S.files.filter((f) => !f.err) : [{ name: "ข้อความ.txt", ext: ".txt", text: S.text }];
  if (!items.length) return;
  const opts = options();
  S.busy = true;
  PR.render();
  const ids = [];
  const ttl = PR.ttlMinutes() * 60;
  for (const it of items) {
    const fd = new FormData();
    if (it.file) fd.append("file", it.file, it.name); else fd.append("text", it.text);
    fd.append("options", JSON.stringify(opts));
    try {
      const r = await PR.api("api/jobs", { method: "POST", body: fd });
      const now = Date.now() / 1000;
      ids.push(r.id);
      it.sent = true;
      PR.hist.put(r.id, { name: it.name, ext: it.ext, kind: kindOf(it.ext), pii: null, mode: "", created: now, expires: now + ttl,
        packs: [...opts.packs] });
    } catch (e) {
      it.err = e.message.length > 40 ? "เปิดไฟล์ไม่ได้" : e.message;
      S.error = `${it.name}: ${e.message}`;
    }
  }
  S.busy = false;
  if (!ids.length) { PR.render(); return; }
  S.files = S.files.filter((f) => !f.sent);
  if (!isFile) S.text = "";
  if (ids.length > 1) PR.toast(`เริ่มตรวจ ${ids.length} ไฟล์ · ไฟล์อื่นอยู่ในงานล่าสุด`);
  PR.go(`#/review/${ids[0]}`);
}

function tabStyle(on) {
  return { fg: on ? "#18181B" : "#71717A", w: on ? "600" : "500", line: on ? "#18181B" : "transparent" };
}
const sw = (on, px = 16) => ({ track: on ? "#18181B" : "#D4D4CF", knob: on ? `translateX(${px}px)` : "none" });

function vals() {
  const cfg = PR.cfg;
  const isFile = S.tab === "file";
  const ft = tabStyle(isFile), pt = tabStyle(!isFile);
  const files = S.files.map((f, i) => ({
    color: PR.extColor(f.ext), ext: PR.extLabel(f.ext), name: f.name,
    meta: `${PR.size(f.size)} · ${PR.kindLabel(kindOf(f.ext))}`,
    stText: f.err || "พร้อมตรวจ", stBg: f.err ? "#FEF2F2" : "#DCFCE7", stFg: f.err ? "#991B1B" : "#166534",
    stIcon: f.err ? "M12 7v6M12 17h.01" : "m5 12 5 5 9-10",
    removeLabel: `เอา ${f.name} ออก`, remove: () => { S.files.splice(i, 1); S.error = ""; },
  }));
  const layers = LAYERS.map((l, i) => {
    const on = l.locked ? true : l.key === "ai" ? S.ai : S.layers[l.key];
    const s = sw(on);
    return {
      label: l.label, desc: l.desc, locked: !!l.locked, switchable: !l.locked, on: String(on), track: s.track, knob: s.knob,
      sep: i === 0 ? "0" : "1px solid #F2F2EE",
      toggle: () => {
        if (l.key === "ai") {
          S.ai = !S.ai;
          if (S.ai && !S.picked.length && (cfg.profiles || []).length) S.picked = [cfg.profiles[0].name];
        } else S.layers[l.key] = !S.layers[l.key];
      },
    };
  });
  const profiles = (cfg.profiles || []).map((p) => {
    const on = S.picked.includes(p.name);
    const model = modelOf(p);
    const lat = PR.store.get(`pr.lat.${p.name}`, null);
    return {
      on: String(on), name: p.name, model: model || "ยังไม่ได้เลือก model", latency: lat != null ? `${lat} s` : "",
      border: on ? "#18181B" : "#E7E7E2", bg: on ? "#FCFCFB" : "#FFFFFF",
      status: p.has_key && model ? "#16A34A" : "#A1A1AA",
      pick: () => {
        S.picked = on ? S.picked.filter((n) => n !== p.name) : S.picked.concat(p.name).slice(-6);
        S.error = "";
      },
    };
  });
  const nPicked = S.picked.length;
  const agree = Math.max(2, Math.min(S.minAgree, nPicked));
  const combines = [
    { key: "union", label: "รวมทุกตัว", tip: "นับทุกจุดที่ model ใดก็ได้พบ" },
    { key: "vote", label: `โหวต ${agree} จาก ${nPicked}`, tip: "นับเฉพาะจุดที่ model เห็นตรงกันอย่างน้อยตามจำนวนนี้" },
  ].map((c, i) => {
    const on = S.combine === c.key;
    return {
      label: c.label, tip: c.tip, on: String(on), sep: i ? "1px solid #E4E4DF" : "0",
      bg: on ? "#18181B" : "#FFFFFF", fg: on ? "#FFFFFF" : "#3F3F46",
      pick: () => {
        if (c.key === "vote" && S.combine === "vote") S.minAgree = agree >= nPicked ? 2 : agree + 1;
        S.combine = c.key;
      },
    };
  });
  const packs = (cfg.packs || []).map((p) => {
    const on = p.locked || S.packs.includes(p.id);
    return {
      name: p.name, short: p.short, on: String(on), locked: p.locked, tip: `${p.law}${p.locked ? " · เปิดเสมอ" : ""}`,
      border: on ? "#18181B" : "#E4E4DF", bg: on ? "#18181B" : "#FFFFFF", fg: on ? "#FFFFFF" : "#3F3F46",
      cursor: p.locked ? "default" : "pointer",
      toggle: () => {
        if (p.locked) return false;
        S.packs = on ? S.packs.filter((x) => x !== p.id) : S.packs.concat(p.id);
      },
    };
  });
  const nTypes = packTypes().length;
  const cur = PR.presetOf(S.packs);
  const presetOpts = [
    ...(cur ? [] : [{ v: "", label: "กำหนดเอง", sel: "selected" }]),
    ...(cfg.presets || []).map((r) => ({ v: r.id, label: `ชุดสำเร็จรูป: ${r.name}`, sel: cur && cur.id === r.id ? "selected" : "" })),
  ];
  const pickPreset = (ev) => {
    const r = (cfg.presets || []).find((x) => x.id === ev.target.value);
    if (r) S.packs = [...r.packs];
  };
  const san = sw(S.sanitize);
  const valid = S.files.filter((f) => !f.err).length;
  const ai = useAI();
  const picked = chosenProfiles();
  const now = Date.now() / 1000;
  const packName = Object.fromEntries((cfg.packs || []).map((p) => [p.id, p.name]));
  const packsOf = (ids) => {   // preset name, or the packs that were on (locked ones are implied)
    if (!Array.isArray(ids)) return { label: "–", tip: "" };
    const on = (cfg.packs || []).filter((p) => p.locked || ids.includes(p.id));
    const tip = on.map((p) => packName[p.id]).join(" · ");
    const r = PR.presetOf(ids);
    if (r) return { label: r.name, tip };
    const named = on.filter((p) => !p.locked).map((p) => p.short);
    return { label: named.length > 2 ? `${named.slice(0, 2).join(" · ")} +${named.length - 2}` : named.join(" · ") || "เฉพาะชุดที่เปิดเสมอ", tip };
  };
  const jobs = PR.hist.all().map((h) => {
    const state = PR.hist.state(h);
    const st = ST[state];
    const live = state !== "gone";
    const pk = packsOf(h.packs);
    return {
      packs: pk.label, packsTip: pk.tip,
      color: PR.extColor(h.ext), ext: PR.extLabel(h.ext), name: h.name, kind: PR.kindLabel(h.kind),
      pii: h.pii == null ? "–" : PR.num(h.pii), mode: h.mode || "–",
      status: state === "gone" ? "หมดเวลา · ลบไฟล์แล้ว" : h.downloaded ? "ดาวน์โหลดแล้ว" : h.exported ? "สร้างไฟล์แล้ว" : "รอตรวจ",
      stBg: st.bg, stFg: st.fg, stDot: st.dot, when: PR.ago(h.created || now),
      live, href: `#/review/${h.id}`, cls: live ? "click" : "",
      open: (ev) => { if (!live || ev.target.closest("a")) return false; PR.go(`#/review/${h.id}`); return false; },
    };
  });
  const mins = PR.ttlMinutes();
  return {
    ttlLabel: mins % 60 === 0 ? `${mins / 60} ชั่วโมง` : `${mins} นาที`,
    isFile, isPaste: !isFile, fileFg: ft.fg, fileW: ft.w, fileLine: ft.line, pasteFg: pt.fg, pasteW: pt.w, pasteLine: pt.line,
    tabFile: () => { S.tab = "file"; S.error = ""; }, tabPaste: () => { S.tab = "paste"; S.error = ""; },
    dropBorder: S.drag ? "#18181B" : "#CFCFC8", dropBg: S.drag ? "#F1F1EE" : "linear-gradient(#FBFBF9, #F7F7F4)",
    onDragOver: (ev) => { ev.preventDefault(); if (S.drag) return false; S.drag = true; },
    onDragLeave: (ev, el) => { if (el.contains(ev.relatedTarget)) return false; S.drag = false; },
    onDrop: (ev) => { ev.preventDefault(); S.drag = false; addFiles(ev.dataTransfer && ev.dataTransfer.files); },
    pickFiles: () => { const el = document.getElementById("filepick"); if (el) el.click(); return false; },
    onPick: (ev) => { addFiles(ev.target.files); },
    accept: cfg.supported.join(","), maxMb: cfg.max_upload_mb,
    files, pasteText: S.text, pasteCount: PR.num(S.text.length),
    onPaste: (ev) => { S.text = ev.target.value; S.error = ""; },
    layers, packs, presetOpts, pickPreset, packNote: `ตรวจ ${nTypes} ประเภทข้อมูล · เปลี่ยนเฉพาะงานนี้ ค่าเริ่มต้นตั้งที่หน้าการตรวจจับ`, aiOn: S.ai, noProfiles: !(cfg.profiles || []).length, profiles,
    multi: S.ai && nPicked > 1, combines,
    combineNote: S.combine === "vote" ? "กดอีกครั้งเพื่อเปลี่ยนจำนวน · มีผลกับผลจาก AI เท่านั้น" : "จับได้มากที่สุด",
    sanitize: String(S.sanitize), sanTrack: san.track, sanKnob: san.knob, toggleSanitize: () => { S.sanitize = !S.sanitize; },
    busy: S.busy,
    startLabel: S.busy ? "กำลังส่งไฟล์…" : isFile ? (valid ? `ตรวจหาข้อมูลส่วนบุคคลใน ${valid} ไฟล์` : "ตรวจหาข้อมูลส่วนบุคคล") : "ตรวจหาข้อมูลส่วนบุคคลในข้อความ",
    startDis: S.busy || (isFile ? !valid : !S.text.trim()) ? "disabled" : "",
    start, error: S.error,
    startNote: ai ? `ใช้ ${picked.map((p) => `${p.name} · ${modelOf(p) || "?"}`).join(", ")} · ประมาณ 20–40 วินาที` : "ไม่ใช้ AI · เสร็จภายในไม่กี่วินาที",
    jobs, noJobs: !jobs.length,
  };
}

PR.views.upload = {
  tpl: "upload",
  title: () => "ตรวจเอกสารใหม่",
  enter() { if (!S.init) init(); S.drag = false; },
  vals,
  after(root) {
    if (PR.pendingScroll === "history") {
      PR.pendingScroll = null;
      const el = root.querySelector("#history");
      if (el) el.scrollIntoView({ block: "start" });
    }
  },
};
})();
