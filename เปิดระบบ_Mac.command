#!/bin/bash
# ดับเบิลคลิกเพื่อเปิดระบบบนเครื่อง Mac
cd "$(dirname "$0")" || exit 1

if ! command -v python3 >/dev/null 2>&1; then
  echo "[!] ยังไม่พบ Python — ติดตั้งจาก https://www.python.org/downloads/"
  read -r -p "กด Enter เพื่อปิด"
  exit 1
fi

if [ ! -x ".venv/bin/python" ]; then
  echo "กำลังเตรียมระบบครั้งแรก (ใช้เวลา 2-5 นาที)..."
  python3 -m venv .venv
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/python -m pip install -r requirements.txt
fi

if [ ! -f ".streamlit/secrets.toml" ]; then
  echo "[i] ไม่พบ .streamlit/secrets.toml — จะเปิดแบบทดลองบนเครื่อง (SQLite) ไม่ใช่ข้อมูลจริงบน Supabase"
  echo "    ถ้าต้องการข้อมูลจริง: คัดลอก .streamlit/secrets.example.toml เป็น secrets.toml แล้วใส่ค่าจริง"
fi

echo "เปิดระบบที่ http://localhost:8501  (ปิดหน้าต่างนี้เพื่อหยุดระบบ)"
.venv/bin/python -m streamlit run app.py
