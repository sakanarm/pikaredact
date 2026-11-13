/* Hero: "ข้อมูลส่วนบุคคล" alternates between the highlight and each redaction mode.
   A scan bar sweeps left to right while the next mode is wiped in behind it. */
(() => {
"use strict";
const box = document.getElementById("heroPii");
if (!box || matchMedia("(prefers-reduced-motion: reduce)").matches) return;
const verbs = document.querySelectorAll("#heroVerb > span");
const chip = document.getElementById("heroChip");
const bar = box.querySelector(".sbar");
const pane = (m) => box.querySelector(`.pv[data-m="${m}"]`);
const CHIP = { hl: "PII", token: "TOKEN · คืนค่าได้", mask: "MASK", remove: "REMOVE", fake: "FAKE DATA" };
const SEQ = ["hl", "token", "hl", "mask", "hl", "remove", "hl", "fake"];
const LATIN = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_#";
let i = 0, cur = "hl";

function scramble(el, final, ms) {   // letters settle one by one, left to right, behind the scan bar
  const t0 = performance.now();
  const step = (now) => {
    const p = Math.min(1, (now - t0) / ms), n = Math.floor(p * final.length);
    let out = final.slice(0, n);
    for (let k = n; k < final.length; k++) out += /[\[\]_ ]/.test(final[k]) ? final[k] : LATIN[(Math.random() * LATIN.length) | 0];
    el.textContent = out;
    if (p < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

const fit = (m) => {   // the box takes the width of what it shows, so the centred line closes up around it
  const w = pane(m).querySelector(".pi").getBoundingClientRect().width;
  if (w) box.style.width = Math.ceil(w) + "px";
};
const h1 = box.closest("h1");
const holdHeight = () => {   // the widest state can take an extra line on phones: keep the page below from jumping
  h1.style.minHeight = "";
  h1.style.minHeight = Math.max(h1.getBoundingClientRect().height, holdHeight.tallest || 0) + "px";
  holdHeight.tallest = parseFloat(h1.style.minHeight);
};
holdHeight();
if (document.fonts) document.fonts.ready.then(() => { if (cur === "hl") { holdHeight.tallest = 0; holdHeight(); } });
addEventListener("resize", () => {
  box.style.transition = "none"; fit(cur); void box.offsetWidth; box.style.transition = "";
  holdHeight.tallest = 0; holdHeight();
});

function show(next) {
  const from = pane(cur), to = pane(next);
  if (!box.style.width) { box.style.width = box.getBoundingClientRect().width + "px"; void box.offsetWidth; }
  if (next === "token") to.querySelector(".pi").textContent = "[PERSON_1]";   // measure the final text
  fit(next);
  from.classList.remove("on");
  from.classList.add("out");
  setTimeout(() => from.classList.remove("out"), 800);
  to.classList.add("on");
  if (next === "hl") {   // replay the marker sweep
    const hl = to.querySelector(".hl-sweep");
    hl.style.animation = "none"; void hl.offsetWidth; hl.style.animation = "";
  }
  if (next === "token") scramble(to.querySelector(".pi"), "[PERSON_1]", 900);
  bar.classList.remove("go"); void bar.offsetWidth; bar.classList.add("go");
  verbs[0].classList.toggle("on", next === "hl");
  verbs[1].classList.toggle("on", next !== "hl");
  chip.textContent = CHIP[next];
  chip.classList.remove("pop"); void chip.offsetWidth; chip.classList.add("pop");
  cur = next;
}

function tick() {
  if (!document.hidden) { i = (i + 1) % SEQ.length; show(SEQ[i]); }
  setTimeout(tick, SEQ[i] === "hl" ? 1900 : 2500);
}
setTimeout(tick, 2600);   // let the first highlight sweep finish
})();

/* Landing page: the hero is static HTML; only the demo card (highlight vs. redacted toggle) is a template.
   The sample text is fictional and lives only in this file. */
(() => {
"use strict";
const PR = window.PR;
const root = document.getElementById("demo");
const tpl = document.getElementById("demo-tpl");
if (!root || !tpl || !PR || !PR.paint) return;
PR.tpl.demo = tpl.textContent;

const T = {
  PERSON: { label: "ชื่อบุคคล", bg: "#FDE7F1", line: "#DB2777", fg: "#9D174D" },
  ORGANIZATION: { label: "ชื่อองค์กร", bg: "#DBEAFE", line: "#2563EB", fg: "#1E40AF" },
  PHONE: { label: "เบอร์โทร", bg: "#E0F2FE", line: "#0369A1", fg: "#075985" },
  EMAIL: { label: "อีเมล", bg: "#CFFAFE", line: "#0E7490", fg: "#155E75" },
  TH_ID_CARD: { label: "เลขบัตรประชาชน", bg: "#FFEDD5", line: "#C2410C", fg: "#9A3412" },
  BANK_ACCOUNT: { label: "เลขบัญชีธนาคาร", bg: "#ECFCCB", line: "#4D7C0F", fg: "#3F6212" },
  IP_ADDRESS: { label: "IP address", bg: "#E2E8F0", line: "#475569", fg: "#334155" },
  SECRET: { label: "รหัสผ่าน / API key", bg: "#FEE2E2", line: "#B91C1C", fg: "#991B1B" },
};
const ORDER = Object.keys(T);
const P = (t) => ({ t });
const X = (t, type, token) => ({ t, type, token });
const RAW = [
  { label: "1 · ข้อมูลโครงการ", parts: [P("เอกสารนี้จัดทำให้ "), X("บริษัท เอบีซี รีเทล จำกัด", "ORGANIZATION", "[ORGANIZATION_1]"), P(" โดยมีคุณ"), X("สมชาย ใจดี", "PERSON", "[PERSON_1]"), P(" ผู้จัดการฝ่ายไอทีเป็นผู้ประสานงานหลัก โทร "), X("081-234-5678", "PHONE", "[PHONE_1]"), P(" อีเมล "), X("somchai.j@example.com", "EMAIL", "[EMAIL_1]")] },
  { label: "2 · ข้อมูลทดสอบ (UAT)", parts: [P("ผู้ใช้ทดสอบ นางสาว"), X("วรรณา ศรีสุข", "PERSON", "[PERSON_2]"), P(" เลขบัตรประชาชน "), X("1-1037-00123-45-8", "TH_ID_CARD", "[TH_ID_CARD_1]"), P(" บัญชีรับเงินเดือน "), X("123-4-56789-0", "BANK_ACCOUNT", "[BANK_ACCOUNT_1]")] },
  { label: "3 · การติดตั้งระบบ", parts: [P("เชื่อมต่อฐานข้อมูลที่ "), X("10.20.30.41", "IP_ADDRESS", "[IP_ADDRESS_1]"), P(" ด้วย DB_PASSWORD="), X("Xk9#mQ2v!r", "SECRET", "████████"), P(" บน Microsoft Azure ตามแผนในบทที่ 5")] },
];
const ON = { bg: "#FFFFFF", fg: "#18181B", sh: "0 1px 2px rgba(0,0,0,.08), 0 0 0 1px #E4E4DF" };
const OFF = { bg: "transparent", fg: "#52525B", sh: "none" };

let view = "highlight";

function vals() {
  const redact = view === "redact";
  let n = 0;
  const counts = {};
  const paras = RAW.map((p) => ({
    label: p.label,
    parts: p.parts.map((x) => {
      if (!x.type) return { pii: false, plain: true, text: x.t };
      n += 1;
      counts[x.type] = (counts[x.type] || 0) + 1;
      const c = T[x.type];
      const secret = x.type === "SECRET";
      return {
        pii: true, plain: false,
        shown: redact ? x.token : x.t,
        bg: redact ? "#18181B" : c.bg,
        fg: redact ? (secret ? "#18181B" : "#FDE047") : c.fg,
        line: redact ? "#18181B" : c.line,
        font: redact ? "'IBM Plex Mono', monospace" : "inherit",
        size: redact ? "0.86em" : "1em",
        delay: (0.3 + n * 0.14).toFixed(2) + "s",
        title: c.label,
      };
    }),
  }));
  const typeRows = ORDER.filter((k) => counts[k]).map((k) => ({
    label: T[k].label,
    dot: T[k].line,
    right: redact ? (k === "SECRET" ? "ลบทิ้ง" : "แทนรหัส") : String(counts[k]),
  }));
  const a = redact ? OFF : ON, b = redact ? ON : OFF;
  return {
    paras, typeRows,
    isHighlight: !redact, isRedact: redact,
    statusChip: redact ? `ปิดบัง ${n} จุด` : `พบ ${n} จุด`,
    sideTitle: redact ? `ปิดบังแล้ว ${n} จุด` : `พบ ${n} จุด · ${typeRows.length} ประเภท`,
    sideSub: redact
      ? "โหมด: แทนด้วยรหัส ค่าเดียวกันได้รหัสเดียวกันทั้งไฟล์ ตรวจซ้ำแล้วไม่พบค่าเดิมหลงเหลือ"
      : "ทุกจุดถูกเลือกไว้ให้แล้ว ตรวจและยกเลิกจุดที่ไม่ใช่ข้อมูลส่วนบุคคลก่อนดาวน์โหลด",
    hlBg: a.bg, hlFg: a.fg, hlShadow: a.sh,
    rdBg: b.bg, rdFg: b.fg, rdShadow: b.sh,
    showHighlight: () => { view = "highlight"; },
    showRedact: () => { view = "redact"; },
  };
}

/* the app renderer repaints #side/#view; here there is only the demo card */
PR.render = () => PR.paint(root, "demo", vals());
PR.bind(root);
PR.render();
})();
