@echo off
chcp 65001 >nul
title ระบบนับการสอนแทน
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel% neq 0 (
  echo [!] ยังไม่พบ Python - ติดตั้งจาก https://www.python.org/downloads/ ^(ติ๊ก Add python to PATH^)
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo กำลังเตรียมระบบครั้งแรก ^(ใช้เวลา 2-5 นาที^)...
  py -3 -m venv .venv
  ".venv\Scripts\python.exe" -m pip install --upgrade pip
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
)

if not exist ".streamlit\secrets.toml" (
  echo [i] ไม่พบ .streamlit\secrets.toml - จะเปิดแบบทดลองบนเครื่อง ^(SQLite^) ไม่ใช่ข้อมูลจริงบน Supabase
  echo     ถ้าต้องการข้อมูลจริง: คัดลอก .streamlit\secrets.example.toml เป็น secrets.toml แล้วใส่ค่าจริง
)

echo เปิดระบบที่ http://localhost:8501  ^(ปิดหน้าต่างนี้เพื่อหยุดระบบ^)
".venv\Scripts\python.exe" -m streamlit run app.py
pause
