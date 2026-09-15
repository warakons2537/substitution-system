from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import List, Optional, Tuple

THAI_MONTHS = {
    "มกราคม": 1, "กุมภาพันธ์": 2, "มีนาคม": 3, "เมษายน": 4,
    "พฤษภาคม": 5, "มิถุนายน": 6, "กรกฎาคม": 7, "สิงหาคม": 8,
    "กันยายน": 9, "ตุลาคม": 10, "พฤศจิกายน": 11, "ธันวาคม": 12,
    "ม.ค.": 1, "ก.พ.": 2, "มี.ค.": 3, "เม.ย.": 4,
    "พ.ค.": 5, "มิ.ย.": 6, "ก.ค.": 7, "ส.ค.": 8,
    "ก.ย.": 9, "ต.ค.": 10, "พ.ย.": 11, "ธ.ค.": 12,
}
THAI_DAYS = ["จันทร์", "อังคาร", "พุธ", "พฤหัสบดี", "ศุกร์", "เสาร์", "อาทิตย์"]


@dataclass
class SubstitutionRecord:
    date_iso: str
    day_name: str
    thai_year: int
    absent_teacher: str
    leave_type: str
    period: int
    subject: str
    class_name: str
    substitute_teacher: str
    acknowledged: str = ""
    note: str = ""
    source_file: str = ""
    confidence: str = "สูง"
    import_method: str = "native"

    def as_dict(self):
        return asdict(self)


def file_sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _norm(text: object) -> str:
    if text is None:
        return ""
    s = str(text).replace("\u00a0", " ").replace("\u200b", "")
    s = s.replace("\u0e4d\u0e32", "\u0e33")  # OCR มักแยก ํ + า แทน ำ
    s = re.sub(r"\s+", " ", s).strip()
    return s


def record_fingerprint(rec: dict) -> str:
    fields = [
        str(rec.get("date_iso", "")), str(rec.get("absent_teacher", "")),
        str(rec.get("period", "")), str(rec.get("subject", "")),
        str(rec.get("class_name", "")), str(rec.get("substitute_teacher", "")),
    ]
    raw = "|".join(_norm(x) for x in fields)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _parse_date_from_text(text: str) -> Optional[date]:
    t = _norm(text)
    month_names = "|".join(sorted((re.escape(k) for k in THAI_MONTHS), key=len, reverse=True))
    patterns = [
        rf"(?:วัน\S*\s*)?(?:ที่|วันที่)\s*(\d{{1,2}})\s*({month_names})\s*(?:พ\.?ศ\.?\s*)?(\d{{2,4}})",
        rf"(\d{{1,2}})\s*({month_names})\s*(?:พ\.?ศ\.?\s*)?(\d{{2,4}})",
    ]
    for p in patterns:
        m = re.search(p, t)
        if not m:
            continue
        d = int(m.group(1)); mo = THAI_MONTHS[m.group(2)]; y = int(m.group(3))
        if y < 100:
            y += 2500 if y >= 40 else 2000
        if y >= 2400:
            y -= 543
        try:
            return date(y, mo, d)
        except ValueError:
            return None
    # fallback วันที่ 7/7/2569 หรือ 07-07-2569
    m = re.search(r"(?:วันที่|ที่)?\s*(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})", t)
    if m:
        d, mo, y = map(int, m.groups())
        if y < 100:
            y += 2500 if y >= 40 else 2000
        if y >= 2400:
            y -= 543
        try:
            return date(y, mo, d)
        except ValueError:
            pass
    return None


def _parse_teacher(text: str) -> Optional[str]:
    t = _norm(text)
    patterns = [
        r"ชื่อ\s*ครู\s*ประจำ\s*วิชา\s*[:：]?\s*(.+)$",
        r"ครู\s*ประจำ\s*วิชา\s*[:：]?\s*(.+)$",
    ]
    for p in patterns:
        m = re.search(p, t)
        if m:
            name = m.group(1).strip(" .:-–—")
            # ตัดข้อความประเภทการลาที่อาจถูก OCR ต่อท้ายบรรทัด
            name = re.split(r"\s+(?:ลาป่วย|ลากิจ|ไปราชการ|ลาคลอด)", name)[0].strip()
            if name and len(name) >= 3:
                return name
    return None


def _parse_leave_type(text: str) -> Optional[str]:
    t = _norm(text)
    checked = r"[✓✔☑☒þÞVv/]"
    for label in ["ลาป่วย", "ลากิจ", "ไปราชการ", "ลาคลอด"]:
        if re.search(checked + rf".{{0,14}}{re.escape(label)}", t):
            return label
        # OCR ของช่องทำเครื่องหมายอาจกลายเป็นเลข 0/๐/2 อยู่ในวงเล็บ
        if re.search(r"\([^)]*[0-9๐-๙][^)]*\)\s*" + re.escape(label), t):
            return label
    return None


def _build_record(
    meta_date: Optional[date], absent_teacher: str, leave_type: str,
    row: List[str], source_file: str, confidence: str = "สูง", import_method: str = "native",
) -> Optional[SubstitutionRecord]:
    if len(row) < 4:
        return None
    period_raw = _norm(row[0])
    m = re.search(r"\d+", period_raw)
    if not m:
        return None
    period = int(m.group(0))
    subject = _norm(row[1]) if len(row) > 1 else ""
    class_name = _norm(row[2]) if len(row) > 2 else ""
    substitute = _norm(row[3]) if len(row) > 3 else ""
    ack = _norm(row[4]) if len(row) > 4 else ""
    note = _norm(row[5]) if len(row) > 5 else ""
    if not subject or not substitute:
        return None
    if not meta_date:
        return SubstitutionRecord(
            date_iso="", day_name="", thai_year=0, absent_teacher=absent_teacher,
            leave_type=leave_type or "ไม่ระบุ", period=period, subject=subject,
            class_name=class_name, substitute_teacher=substitute,
            acknowledged=ack, note=note, source_file=source_file,
            confidence="ต้องตรวจสอบ", import_method=import_method,
        )
    return SubstitutionRecord(
        date_iso=meta_date.isoformat(), day_name=THAI_DAYS[meta_date.weekday()],
        thai_year=meta_date.year + 543, absent_teacher=absent_teacher,
        leave_type=leave_type or "ไม่ระบุ", period=period, subject=subject,
        class_name=class_name, substitute_teacher=substitute,
        acknowledged=ack, note=note, source_file=source_file,
        confidence=confidence, import_method=import_method,
    )


def _header_looks_like_substitution(cells: List[str]) -> bool:
    if not (5 <= len(cells) <= 8):
        return False
    c = [_norm(x) for x in cells]
    joined = " ".join(c)
    first = c[0] if c else ""
    if "หมายเหตุ" in joined and 5 <= len(c) <= 8:
        return True
    return (("ชั่วโมง" in first or "ชั่ว" in first or "คาบ" in first) and
            "วิชา" in joined and "ชั้น" in joined and
            ("ผู้สอนแทน" in joined or ("ผู้" in joined and "สอน" in joined)))


def _find_soffice() -> Optional[str]:
    candidates = [
        shutil.which("soffice"), shutil.which("libreoffice"),
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
        "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    ]
    for c in candidates:
        if c and os.path.exists(c):
            return c
    return None


def libreoffice_status() -> tuple[bool, str]:
    p = _find_soffice()
    return (bool(p), p or "ไม่พบ LibreOffice")


def convert_doc_to_docx(path: str | Path) -> Path:
    soffice = _find_soffice()
    if not soffice:
        raise RuntimeError("ไฟล์ .doc ต้องใช้ LibreOffice เพื่อแปลงเป็น .docx ก่อนอ่าน แต่เครื่องนี้ยังไม่พบ LibreOffice")
    tmp = Path(tempfile.mkdtemp(prefix="subdoc_"))
    cmd = [soffice, "--headless", "--convert-to", "docx", "--outdir", str(tmp), str(path)]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=90)
    outs = list(tmp.glob("*.docx"))
    if not outs:
        raise RuntimeError("แปลง .doc ไม่สำเร็จ: " + result.stdout[-600:])
    return outs[0]


def parse_docx(path: str | Path) -> Tuple[List[dict], List[str]]:
    from docx import Document
    from docx.document import Document as _Document
    from docx.oxml.table import CT_Tbl
    from docx.oxml.text.paragraph import CT_P
    from docx.table import Table, _Cell
    from docx.text.paragraph import Paragraph

    def iter_block_items(parent):
        if isinstance(parent, _Document):
            parent_elm = parent.element.body
        elif isinstance(parent, _Cell):
            parent_elm = parent._tc
        else:
            raise ValueError("unsupported parent")
        for child in parent_elm.iterchildren():
            if isinstance(child, CT_P):
                yield Paragraph(child, parent)
            elif isinstance(child, CT_Tbl):
                yield Table(child, parent)

    doc = Document(str(path))
    records: List[dict] = []
    warnings: List[str] = []
    current_date: Optional[date] = None
    current_teacher = ""
    current_leave = "ไม่ระบุ"
    seen_substitution_heading = False

    for item in iter_block_items(doc):
        if isinstance(item, Paragraph):
            text = _norm(item.text)
            if not text:
                continue
            if "บันทึกการสอนแทน" in text:
                seen_substitution_heading = True
                continue
            if seen_substitution_heading:
                d = _parse_date_from_text(text)
                if d:
                    current_date = d
                teacher = _parse_teacher(text)
                if teacher:
                    current_teacher = teacher
                leave = _parse_leave_type(text)
                if leave:
                    current_leave = leave
        else:
            rows = [[_norm(c.text) for c in r.cells] for r in item.rows]
            if not rows:
                continue
            header_idx = next((i for i, r in enumerate(rows[:3]) if _header_looks_like_substitution(r)), None)
            if header_idx is not None:
                if not current_teacher:
                    warnings.append("พบตารางสอนแทนแต่ไม่พบชื่อครูประจำวิชาในช่วงก่อนหน้า")
                if not current_date:
                    warnings.append("พบตารางสอนแทนแต่ไม่พบวันที่ในช่วงก่อนหน้า")
                for row in rows[header_idx + 1:]:
                    rec = _build_record(current_date, current_teacher, current_leave, row, Path(path).name, "สูง", "docx")
                    if rec:
                        records.append(rec.as_dict())
    if not records:
        warnings.append("ไม่พบตารางรูปแบบ 'บันทึกการสอนแทน' ที่อ่านได้")
    return records, warnings


def _clean_pdf_cell(value: object) -> str:
    s = _norm(value).replace("\n", "")
    s = re.sub(r"\(cid:\d+\)", "", s)
    return _norm(s)


def parse_pdf_native(path: str | Path) -> Tuple[List[dict], List[str], int]:
    import pdfplumber

    records: List[dict] = []
    warnings: List[str] = []
    current_date: Optional[date] = None
    current_teacher = ""
    current_leave = "ไม่ระบุ"
    total_text_chars = 0
    continuation_allowed = False

    with pdfplumber.open(str(path)) as pdf:
        for page_no, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            total_text_chars += len(re.sub(r"\s+", "", text))
            for line in text.splitlines():
                d = _parse_date_from_text(line)
                if d: current_date = d
                teacher = _parse_teacher(line)
                if teacher: current_teacher = teacher
                leave = _parse_leave_type(line)
                if leave: current_leave = leave

            tables = page.extract_tables() or []
            for tbl in tables:
                clean = [[_clean_pdf_cell(c) for c in (r or [])] for r in tbl if r]
                if not clean:
                    continue
                header_idx = next((i for i, row in enumerate(clean[:4]) if _header_looks_like_substitution(row)), None)
                if header_idx is None:
                    likely_data = clean and all((not r) or re.fullmatch(r"\d+", _norm(r[0] if r else "")) for r in clean)
                    if likely_data and continuation_allowed and current_date:
                        header_idx = -1
                    else:
                        continuation_allowed = False
                        continue
                else:
                    continuation_allowed = True
                for row in clean[header_idx + 1:]:
                    rec = _build_record(current_date, current_teacher, current_leave, row, Path(path).name, "ปานกลาง", "pdf-native")
                    if rec:
                        records.append(rec.as_dict())

    if records and any(not r.get("absent_teacher") for r in records):
        warnings.append("PDF บางหน้าดึงชื่อครูประจำวิชาไม่ได้ กรุณาเติมชื่อในหน้าตรวจสอบก่อนนำเข้า")
    return records, warnings, total_text_chars


def tesseract_status() -> dict:
    status = {"available": False, "path": "", "thai": False, "version": "", "message": ""}
    try:
        import pytesseract
        cmd = shutil.which("tesseract")
        if not cmd:
            # common Windows path
            win = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
            if os.path.exists(win):
                cmd = win
                pytesseract.pytesseract.tesseract_cmd = win
        if not cmd:
            status["message"] = "ไม่พบโปรแกรม Tesseract OCR"
            return status
        status["path"] = cmd
        status["version"] = str(pytesseract.get_tesseract_version()).splitlines()[0]
        langs = set(pytesseract.get_languages(config=""))
        status["thai"] = "tha" in langs or "Thai" in langs
        status["available"] = True
        status["message"] = "พร้อมใช้งาน" if status["thai"] else "พบ Tesseract แต่ยังไม่มีภาษาไทย (tha)"
        return status
    except Exception as exc:
        status["message"] = str(exc)
        return status


def _fuzzy_contains(text: str, target: str) -> bool:
    from difflib import SequenceMatcher
    a = re.sub(r"\s+", "", _norm(text))
    b = re.sub(r"\s+", "", target)
    if not a:
        return False
    return b in a or SequenceMatcher(None, a, b).ratio() >= 0.62


def _compact_ocr_cell(text: str) -> str:
    s = _norm(text)
    parts = s.split()
    if len(parts) >= 4 and sum(1 for p in parts if len(p) <= 2) / len(parts) >= 0.6:
        return "".join(parts)
    return s


def _parse_ocr_text_rows(full_text: str, meta_date: Optional[date], absent_teacher: str, leave_type: str, source_file: str) -> list[dict]:
    records: list[dict] = []
    for raw in full_text.splitlines():
        line = _norm(raw).strip(" |:;-")
        m = re.match(r"^(\d{1,2})\s*[|Il]?\s*(.+)$", line)
        if not m:
            continue
        period = int(m.group(1))
        if not (1 <= period <= 20):
            continue
        rest = _norm(m.group(2))
        cm = re.search(r"((?:ป|ม)\s*\.?\s*\d+|อนุบาล\s*\d+)", rest)
        if not cm:
            continue
        subject = rest[:cm.start()].strip(" |:-")
        class_name = re.sub(r"\s+", "", cm.group(1))
        tail = rest[cm.end():].strip(" |:-")
        note = ""
        nm = re.search(r"(ทำ\s*ใบ\s*งาน.*)$", tail)
        if nm:
            note = re.sub(r"\s+", "", nm.group(1))
            substitute = tail[:nm.start()].strip(" |:-")
        else:
            substitute = tail
        substitute = _compact_ocr_cell(substitute)
        subject = _compact_ocr_cell(subject)
        if not re.match(r"^(?:ครู|นาย|นาง|นางสาว)", substitute):
            continue
        row = [str(period), subject, class_name, substitute, "", note]
        rec = _build_record(meta_date, absent_teacher, leave_type, row, source_file, "OCR-ปานกลาง", "pdf-ocr")
        if rec:
            records.append(rec.as_dict())
    return records


def _ocr_page_records(image, source_file: str, page_no: int, previous_date: Optional[date] = None, previous_teacher: str = "", previous_leave: str = "ไม่ระบุ") -> tuple[list[dict], list[str], Optional[date], str, str]:
    import pandas as pd
    import pytesseract
    from pytesseract import Output

    warnings: list[str] = []
    config = "--oem 3 --psm 4"
    full_text = pytesseract.image_to_string(image, lang="tha+eng", config=config)
    meta_date = _parse_date_from_text(full_text) or previous_date
    absent_teacher = previous_teacher
    for line in full_text.splitlines():
        t = _parse_teacher(line)
        if t:
            absent_teacher = t
            break
    leave_type = _parse_leave_type(full_text) or previous_leave or "ไม่ระบุ"

    data = pytesseract.image_to_data(image, lang="tha+eng", config=config, output_type=Output.DATAFRAME)
    if data is None or data.empty:
        return [], [f"OCR หน้า {page_no}: ไม่พบข้อความ"], meta_date, absent_teacher, leave_type
    data = data.copy()
    data["text"] = data["text"].fillna("").astype(str).map(_norm)
    data["conf"] = pd.to_numeric(data["conf"], errors="coerce").fillna(-1)
    data = data[(data["text"] != "") & (data["conf"] >= 10)].copy()
    if data.empty:
        return [], [f"OCR หน้า {page_no}: ความมั่นใจต่ำเกินไป"], meta_date, absent_teacher, leave_type

    width, height = image.size
    data["cx"] = data["left"] + data["width"] / 2
    data["cy"] = data["top"] + data["height"] / 2

    # หาแถวหัวตารางและตำแหน่งลายเซ็นเพื่อจำกัดช่วง
    header_candidates = data[data["text"].map(lambda x: any(_fuzzy_contains(x, k) for k in ["วิชา", "ชั้น", "ผู้สอนแทน", "หมายเหตุ"]))]
    header_y = float(header_candidates["cy"].median()) if len(header_candidates) >= 2 else None
    sign_candidates = data[data["text"].map(lambda x: _fuzzy_contains(x, "ลงชื่อ"))]
    sign_y = None
    if not sign_candidates.empty:
        ys = sorted(float(x) for x in sign_candidates["cy"].tolist())
        if header_y is not None:
            ys = [y for y in ys if y > header_y]
        if ys:
            sign_y = ys[0]

    # หมายเลขคาบในคอลัมน์ซ้าย
    period_words = data[
        data["text"].str.fullmatch(r"(?:[1-9]|1\d|20)", na=False)
        & (data["cx"] < width * 0.22)
    ].copy()
    if header_y is not None:
        period_words = period_words[period_words["cy"] > header_y + 2]
    if sign_y is not None:
        period_words = period_words[period_words["cy"] < sign_y - 3]

    # ถ้ามีเลขซ้ำจาก OCR ให้เลือกตัวที่ซ้ายสุด/มั่นใจสุดต่อคาบ
    # หน้า continuation บางแบบ PSM 4 อ่านข้อความได้แต่ไม่เห็นเลขคาบ (เช่นคาบ 5-6)
    # ลอง PSM 6 เพิ่มเฉพาะเมื่อเลขคาบที่พบมีน้อยมาก
    if len(period_words) < 2:
        config6 = "--oem 3 --psm 6"
        full_text6 = pytesseract.image_to_string(image, lang="tha+eng", config=config6)
        # เก็บ metadata เดิมจากหน้าก่อนหน้า แต่รับค่าที่อ่านได้เพิ่มจาก PSM 6
        meta_date = _parse_date_from_text(full_text6) or meta_date
        for line in full_text6.splitlines():
            t = _parse_teacher(line)
            if t:
                absent_teacher = t
                break
        leave_type = _parse_leave_type(full_text6) or leave_type
        data6 = pytesseract.image_to_data(image, lang="tha+eng", config=config6, output_type=Output.DATAFRAME)
        if data6 is not None and not data6.empty:
            data6 = data6.copy()
            data6["text"] = data6["text"].fillna("").astype(str).map(_norm)
            data6["conf"] = pd.to_numeric(data6["conf"], errors="coerce").fillna(-1)
            data6 = data6[(data6["text"] != "") & (data6["conf"] >= 10)].copy()
            if not data6.empty:
                data = data6
                data["cx"] = data["left"] + data["width"] / 2
                data["cy"] = data["top"] + data["height"] / 2
                header_candidates = data[data["text"].map(lambda x: any(_fuzzy_contains(x, k) for k in ["วิชา", "ชั้น", "ผู้สอนแทน", "หมายเหตุ"]))]
                header_y = float(header_candidates["cy"].median()) if len(header_candidates) >= 2 else None
                sign_candidates = data[data["text"].map(lambda x: _fuzzy_contains(x, "ลงชื่อ"))]
                sign_y = None
                if not sign_candidates.empty:
                    ys = sorted(float(x) for x in sign_candidates["cy"].tolist())
                    if header_y is not None:
                        ys = [y for y in ys if y > header_y]
                    if ys:
                        sign_y = ys[0]
                period_words = data[data["text"].str.fullmatch(r"(?:[1-9]|1\d|20)", na=False) & (data["cx"] < width * 0.22)].copy()
                if header_y is not None:
                    period_words = period_words[period_words["cy"] > header_y + 2]
                if sign_y is not None:
                    period_words = period_words[period_words["cy"] < sign_y - 3]
                if full_text6.strip():
                    full_text = full_text + "\n" + full_text6

    anchors = []
    for period, g in period_words.groupby(period_words["text"].astype(int)):
        if not (1 <= int(period) <= 20):
            continue
        g = g.sort_values(["cx", "conf"], ascending=[True, False])
        row = g.iloc[0]
        anchors.append((int(period), float(row["cy"]), float(row["conf"])))
    anchors.sort(key=lambda x: x[1])
    if len(anchors) < 2:
        text_records = _parse_ocr_text_rows(full_text, meta_date, absent_teacher, leave_type, source_file)
        return text_records, ([] if text_records else [f"OCR หน้า {page_no}: ไม่พบโครงสร้างแถวคาบเรียนเพียงพอ"]), meta_date, absent_teacher, leave_type

    # ตัด anchor ที่ลำดับ y ผิดรุนแรง/ซ้ำใกล้กัน
    cleaned = []
    seen_periods = set()
    for a in anchors:
        if a[0] in seen_periods:
            continue
        seen_periods.add(a[0]); cleaned.append(a)
    anchors = cleaned

    # boundaries ตามแบบฟอร์มมาตรฐาน: คาบ | วิชา | ชั้น | ผู้สอนแทน | รับทราบ | หมายเหตุ
    xbounds = [0.00, 0.135, 0.385, 0.515, 0.705, 0.845, 1.01]
    xbounds = [x * width for x in xbounds]

    text_records = _parse_ocr_text_rows(full_text, meta_date, absent_teacher, leave_type, source_file)
    records: list[dict] = list(text_records)
    text_periods = {int(r.get("period", 0)) for r in text_records}
    centers = [a[1] for a in anchors]
    for idx, (period, cy, anchor_conf) in enumerate(anchors):
        top = (header_y + 3) if idx == 0 and header_y is not None else (centers[idx - 1] + cy) / 2 if idx > 0 else cy - 45
        bottom = (centers[idx + 1] + cy) / 2 if idx < len(centers) - 1 else (sign_y - 3 if sign_y is not None else cy + 45)
        row_words = data[(data["cy"] >= top) & (data["cy"] < bottom)].copy()
        cells = [str(period)]
        cell_confs = [anchor_conf]
        for col in range(1, 6):
            left, right = xbounds[col], xbounds[col + 1]
            words = row_words[(row_words["cx"] >= left) & (row_words["cx"] < right)].copy()
            if words.empty:
                cells.append(""); continue
            words = words.sort_values(["top", "left"])
            txt = _norm(" ".join(words["text"].tolist()))
            cells.append(txt)
            cell_confs.extend(words["conf"].tolist())

        # ทำความสะอาด label ที่หลุดเข้าคอลัมน์
        cells[1] = _compact_ocr_cell(re.sub(r"^(?:วิชา)\s*", "", cells[1]).strip(" |:-"))
        cells[2] = re.sub(r"\s+", "", re.sub(r"^(?:ชั้น)\s*", "", cells[2]).strip(" |:-"))
        cells[3] = _compact_ocr_cell(re.sub(r"^(?:ผู้สอนแทน)\s*", "", cells[3]).strip(" |:-"))
        cells[5] = _compact_ocr_cell(re.sub(r"^(?:หมายเหตุ)\s*", "", cells[5]).strip(" |:-"))

        # ลด false positive จากแถวว่าง/ข้อความหัวเอกสาร
        valid_class = bool(re.match(r"^(?:(?:ป|ม)\.?\d+|อนุบาล\d+)$", cells[2]))
        valid_teacher = bool(re.match(r"^(?:ครู|นาย|นาง|นางสาว)", cells[3]))
        if period in text_periods or not (valid_class and valid_teacher):
            continue

        avg_conf = sum(float(x) for x in cell_confs if float(x) >= 0) / max(1, len([x for x in cell_confs if float(x) >= 0]))
        conf_label = "OCR-ปานกลาง" if avg_conf >= 55 else "OCR-ต่ำ/ต้องตรวจสอบ"
        rec = _build_record(meta_date, absent_teacher, leave_type, cells, source_file, conf_label, "pdf-ocr")
        if rec:
            records.append(rec.as_dict())

    if records and not meta_date:
        warnings.append(f"OCR หน้า {page_no}: อ่านวันที่ไม่ได้ กรุณาเติมวันที่ก่อนนำเข้า")
    if records and not absent_teacher:
        warnings.append(f"OCR หน้า {page_no}: อ่านชื่อครูประจำวิชาไม่ได้ กรุณาเติมชื่อก่อนนำเข้า")
    if records and leave_type == "ไม่ระบุ":
        warnings.append(f"OCR หน้า {page_no}: ประเภทการลาไม่ชัดเจน กรุณาตรวจสอบ")
    return records, warnings, meta_date, absent_teacher, leave_type


def parse_pdf_ocr(path: str | Path, dpi: int = 220) -> Tuple[List[dict], List[str]]:
    status = tesseract_status()
    if not status["available"]:
        raise RuntimeError("ยังไม่พบ Tesseract OCR ในเครื่อง จึงอ่าน PDF สแกนไม่ได้")
    if not status["thai"]:
        raise RuntimeError("Tesseract OCR ยังไม่มีชุดภาษาไทย (tha) กรุณาติดตั้งภาษาไทยก่อน")

    import fitz
    from PIL import Image, ImageOps

    records: list[dict] = []
    warnings: list[str] = ["ไฟล์นี้ใช้ OCR: โปรดตรวจข้อมูลทุกแถวก่อนกดยืนยันนำเข้า"]
    doc = fitz.open(str(path))
    scale = dpi / 72.0
    matrix = fitz.Matrix(scale, scale)
    current_date: Optional[date] = None
    current_teacher = ""
    current_leave = "ไม่ระบุ"
    for page_no, page in enumerate(doc, start=1):
        pix = page.get_pixmap(matrix=matrix, alpha=False, colorspace=fitz.csGRAY)
        image = Image.frombytes("L", [pix.width, pix.height], pix.samples)
        image = ImageOps.autocontrast(image)
        recs, warns, current_date, current_teacher, current_leave = _ocr_page_records(
            image, Path(path).name, page_no, current_date, current_teacher, current_leave
        )
        records.extend(recs); warnings.extend(warns)
    doc.close()
    if not records:
        warnings.append("OCR ทำงานแล้วแต่ยังไม่พบรายการสอนแทน กรุณากรอกด้วยมือหรือใช้ไฟล์ต้นฉบับ .docx")
    return records, warnings


def parse_pdf(path: str | Path, enable_ocr: bool = False, force_ocr: bool = False) -> Tuple[List[dict], List[str]]:
    if force_ocr:
        return parse_pdf_ocr(path)
    native_records, native_warnings, text_chars = parse_pdf_native(path)
    if native_records:
        return native_records, native_warnings
    if enable_ocr:
        recs, warnings = parse_pdf_ocr(path)
        if text_chars > 100:
            warnings.insert(0, "PDF มีข้อความแต่ตัวอ่านตารางปกติไม่พบรายการ จึงทดลอง OCR เพิ่ม")
        return recs, native_warnings + warnings
    native_warnings.append("ไม่พบตารางสอนแทนจาก PDF อัตโนมัติ หากเป็น PDF สแกนให้เปิดตัวเลือก OCR")
    return [], native_warnings


def parse_document(
    path: str | Path, enable_ocr: bool = False, force_ocr: bool = False,
) -> Tuple[List[dict], List[str], Optional[Path]]:
    path = Path(path)
    ext = path.suffix.lower()
    converted: Optional[Path] = None
    if ext == ".doc":
        converted = convert_doc_to_docx(path)
        recs, warnings = parse_docx(converted)
        for r in recs:
            r["source_file"] = path.name
            r["import_method"] = "doc-converted"
        return recs, warnings, converted
    if ext == ".docx":
        recs, warnings = parse_docx(path)
        return recs, warnings, None
    if ext == ".pdf":
        recs, warnings = parse_pdf(path, enable_ocr=enable_ocr, force_ocr=force_ocr)
        return recs, warnings, None
    raise ValueError("รองรับเฉพาะ .doc, .docx และ .pdf")
