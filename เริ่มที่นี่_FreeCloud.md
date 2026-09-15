# เริ่มที่นี่ — V4.1 FreeCloud

เวอร์ชันนี้เตรียมไว้สำหรับ **Streamlit Community Cloud + Supabase** โดยไม่ต้องมี VM, SSH หรือ Docker

## ขั้นที่ 1 — สร้าง Supabase ฟรี

1. เข้า Supabase และสร้าง Project ใหม่
2. ตั้ง Database Password ที่เดายากและเก็บไว้
3. รอ Project พร้อมใช้งาน
4. กด **Connect** ด้านบนของ Project
5. เลือก **Session pooler**
6. คัดลอก URI (พอร์ต 5432)
7. แทน `[YOUR-PASSWORD]` ด้วย Database Password ของคุณ

> แนะนำ Session pooler เพราะใช้ IPv4 และเหมาะกับแอป backend ที่ทำงานต่อเนื่องอย่าง Streamlit

## ขั้นที่ 2 — อัปโปรเจกต์ขึ้น GitHub

สร้าง Repository ใหม่ แล้วอัปไฟล์ทั้งหมดในโฟลเดอร์นี้ขึ้น GitHub

**สำคัญ:** อย่าอัป `.streamlit/secrets.toml` หรือรหัสผ่านใด ๆ ขึ้น GitHub

## ขั้นที่ 3 — Deploy Streamlit

1. เข้า Streamlit Community Cloud
2. เชื่อม GitHub
3. Create app → เลือก repository นี้
4. Main file path = `app.py`
5. เปิด **Advanced settings**
6. Python version = **3.12**
7. Secrets: ใช้ `.streamlit/secrets.example.toml` เป็นแม่แบบ แล้วใส่ค่าจริง
8. Deploy

`packages.txt` จะติดตั้ง Tesseract ภาษาไทย + LibreOffice ให้อัตโนมัติ

## ขั้นที่ 4 — Login ครั้งแรก

ใช้ Username และ Password ที่กำหนดใน Secrets:

- `BOOTSTRAP_ADMIN_USERNAME`
- `BOOTSTRAP_ADMIN_PASSWORD`

ระบบจะบังคับเปลี่ยนรหัสผ่านทันทีหลัง Login ครั้งแรก

## ขั้นที่ 5 — Supabase Storage (ทำทีหลังได้)

ถ้ายังไม่ตั้ง Storage ระบบยังอัปโหลดและอ่านเอกสารได้ และข้อมูลที่สกัดแล้วจะบันทึกใน PostgreSQL ตามปกติ แต่ **ไฟล์ต้นฉบับที่เก็บบน local ของ Streamlit Cloud ไม่ควรถือว่าเป็นข้อมูลถาวร**

เมื่อต้องการเก็บไฟล์ต้นฉบับถาวร:

1. Supabase → Storage → สร้าง bucket เช่น `substitution-documents`
2. เปิดหน้า S3 configuration ของ Storage
3. สร้าง S3 access keys
4. นำ Endpoint, Region, Access Key, Secret Key มาใส่ใน Streamlit Secrets ตามตัวอย่าง
5. Reboot app

## หากขึ้น Error เชื่อมฐานข้อมูล

ตรวจ 4 อย่างก่อน:

1. ใช้ **Session pooler** ไม่ใช่ Direct connection
2. พอร์ตเป็น `5432`
3. Database Password ถูกต้อง
4. URI มี `?sslmode=require`

## สิ่งที่ V4.1 ปรับจาก V4

- ปิด sample 15 คาบเป็นค่าเริ่มต้น
- เวลา Audit Log ใช้ `Asia/Bangkok`
- Cloud mode บังคับตั้ง Admin password เอง
- PostgreSQL connection pool ขนาดเล็ก เหมาะกับ Supabase Free
- Supabase S3 ใช้ Signature V4 + path-style
- เพิ่ม `packages.txt` สำหรับ Streamlit Community Cloud
- จำกัดไฟล์อัปโหลด 50 MB เพื่อลดปัญหา RAM บน free tier
- เพิ่ม `.gitignore` กัน secret/database หลุดขึ้น GitHub
