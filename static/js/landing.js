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
