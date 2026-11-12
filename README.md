# PikaRedact

ตรวจหาและปิดบังข้อมูลส่วนบุคคลภาษาไทยในเอกสาร Word / Excel / PowerPoint / PDF / ข้อความ
ขั้นตอน: อัปโหลด → ตรวจ → คนตรวจทาน (ยกเลิก/เพิ่ม) → เลือก **ไฮไลต์** หรือ **ปิดบัง** → ดาวน์โหลด

## ชั้นการตรวจ
1. กฎ + checksum (เลขบัตรประชาชน บัตรเครดิต เบอร์โทร บัญชี อีเมล IP ฯลฯ)
2. NLP ภาษาไทย (PyThaiNLP) สำหรับชื่อและที่อยู่
3. รายชื่อขององค์กร (บุคคล / องค์กร / คำยกเว้น) — แก้ได้ในหน้า “รายชื่อ”
4. LLM ภายใน — เลือกได้หลาย connector/โมเดลพร้อมกัน
   - **รวมทุกผล (union)**: เก็บทุกอย่างที่โมเดลใดก็ได้พบ (เน้นไม่พลาด) โมเดลเห็นตรงกันได้คะแนนเพิ่ม
   - **โหวต (vote)**: สิ่งที่ LLM พบอย่างเดียวต้องมีอย่างน้อย N โมเดลเห็นตรงกัน

## วิธีปิดบัง
`remove` (████) · `mask` (081-xxx-xx78) · `token` ([PERSON_1] คืนค่าได้ด้วยไฟล์ .piimap ที่เข้ารหัสในเบราว์เซอร์) · `fake` (ข้อมูลปลอมภาษาไทย)

## รันในเครื่อง
```bash
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt   # Linux: .venv/bin/pip
cp .env.example .env        # ใส่ LLM endpoint ถ้าต้องการ
.venv/Scripts/python -m uvicorn app.main:app --port 8020
# เปิด http://127.0.0.1:8020/
pytest -q tests
```

## Deploy (pm2 + nginx)
```bash
git clone <repo> ~/pikaredact && cd ~/pikaredact
python3 -m venv venv && venv/bin/pip install -r requirements.txt
cp .env.example .env && chmod 600 .env   # ใส่ค่าจริง
pm2 start deploy/pikaredact.config.js && pm2 save
curl -s 127.0.0.1:8020/health
```
จากนั้น include `deploy/nginx-pikaredact.conf` ใน server block ของ nginx แล้ว `sudo nginx -t && sudo systemctl reload nginx`
แอปจะอยู่ที่ `https://<host>/PikaRedact/` (ต้องเป็น HTTPS เพื่อให้เข้ารหัส .piimap ในเบราว์เซอร์ได้)

## API
- `POST /api/jobs` (file หรือ text) → `GET /api/jobs/{id}` → `POST /api/jobs/{id}/export`
- `POST /analyze`, `POST /anonymize` — รูปแบบเดียวกับ Presidio ใช้กับ LiteLLM Presidio guardrail ได้
- เอกสาร API: `/api/docs`

## ความเป็นส่วนตัว
ไฟล์และผลตรวจอยู่ในหน่วยความจำเท่านั้น หมดอายุตาม `JOB_TTL_SECONDS` สถิติเก็บเฉพาะจำนวน ไม่เก็บค่า
คีย์และ URL ของ LLM อยู่ใน `.env` บนเซิร์ฟเวอร์ ไม่ส่งไปที่เบราว์เซอร์

ใช้ PyMuPDF (AGPL) สำหรับ PDF
