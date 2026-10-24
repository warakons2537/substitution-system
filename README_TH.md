# ระบบนับและสรุปการสอนแทน V4.2.5 FreeCloud

> **เปิดระบบไม่ได้ / จำวิธีเปิดไม่ได้ → อ่าน `วิธีเปิดระบบ.md`**

เวอร์ชันสำหรับขึ้นออนไลน์แบบง่ายและใช้ฟรี โดยใช้:

- **Streamlit Community Cloud** — ตัวเว็บ
- **Supabase PostgreSQL** — ฐานข้อมูลกลาง
- **Supabase Storage (S3-compatible)** — เก็บไฟล์ต้นฉบับ (เพิ่มภายหลังได้)
- **GitHub** — เก็บ source code

ไม่ต้องสร้าง VM, ไม่ต้อง SSH, ไม่ต้อง Docker และไม่ต้องเปิด Port เอง

อ่านขั้นตอนติดตั้งครั้งแรกที่ **`เริ่มที่นี่_FreeCloud.md`** · วิธีเปิดใช้งาน/แก้ปัญหาที่ **`วิธีเปิดระบบ.md`**

## ไฟล์สำคัญสำหรับ Cloud

- `app.py` — Main file
- `requirements.txt` — Python packages
- `packages.txt` — Tesseract ภาษาไทย / LibreOffice / Thai fonts
- `.streamlit/config.toml` — Streamlit settings
- `.streamlit/secrets.example.toml` — แม่แบบ Secrets (ไม่มีรหัสจริง)
- `.gitignore` — ป้องกัน secrets และฐาน local ถูก commit

## Local/Test

ถ้าไม่กำหนด `DATABASE_URL` ระบบยังใช้ SQLite บนเครื่องได้เหมือนเดิม สำหรับ local/test เท่านั้น

> ใน FreeCloud mode (`FREECLOUD_MODE=1`) ระบบจะไม่ยอมสร้าง Admin ด้วยรหัส default ถ้าไม่ได้กำหนด `BOOTSTRAP_ADMIN_PASSWORD`
