from database import database_kind, safe_database_url
from parser_engine import libreoffice_status, tesseract_status
from storage import storage_status

ocr = tesseract_status()
lo_ok, lo = libreoffice_status()
st = storage_status()
print("=== ตรวจสอบระบบนับการสอนแทน V4 ===")
print("Database:", database_kind())
print("DATABASE_URL:", safe_database_url())
print("Tesseract OCR:", ocr.get("message"))
print("ภาษาไทย (tha):", "พร้อม" if ocr.get("thai") else "ยังไม่พร้อม")
print("LibreOffice:", "พร้อม" if lo_ok else "ยังไม่พบ")
print("LibreOffice path:", lo)
print("Storage:", st)
