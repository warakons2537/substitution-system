from __future__ import annotations

import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any


DAYS_TH = ["จันทร์", "อังคาร", "พุธ", "พฤหัสบดี", "ศุกร์"]
DAY_SET = set(DAYS_TH)


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    s = str(value).replace("\xa0", " ").strip()
    s = re.sub(r"\s+", " ", s)
    if s in {"#N/A", "#NAME?", "#VALUE!", "nan", "None"}:
        return ""
    return s


def normalize_teacher(name: Any) -> str:
    return clean_text(name)


def _col_num(cell_ref: str) -> int:
    m = re.match(r"([A-Z]+)", cell_ref.upper())
    if not m:
        return 0
    n = 0
    for ch in m.group(1):
        n = n * 26 + (ord(ch) - 64)
    return n


def _read_ooxml_sheet(path: str | Path, sheet_name: str) -> dict[int, dict[int, Any]]:
    """Read cached values from xlsx/xlsm without evaluating formulas."""
    path = str(path)
    with zipfile.ZipFile(path, "r") as zf:
        ns = {
            "a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
            "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
            "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
        }
        shared: list[str] = []
        if "xl/sharedStrings.xml" in zf.namelist():
            root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            for si in root.findall("a:si", ns):
                parts = []
                for node in si.iter("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t"):
                    parts.append(node.text or "")
                shared.append("".join(parts))

        wb = ET.fromstring(zf.read("xl/workbook.xml"))
        rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
        relmap = {r.attrib["Id"]: r.attrib["Target"] for r in rels}
        target = None
        for s in wb.find("a:sheets", ns):
            if s.attrib.get("name") == sheet_name:
                rid = s.attrib["{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"]
                target = relmap[rid]
                break
        if not target:
            raise ValueError(f"ไม่พบชีต '{sheet_name}'")
        if target.startswith("/"):
            sheet_path = target.lstrip("/")
        else:
            sheet_path = "xl/" + target.lstrip("/")

        root = ET.fromstring(zf.read(sheet_path))
        out: dict[int, dict[int, Any]] = {}
        for row in root.findall(".//a:sheetData/a:row", ns):
            rnum = int(row.attrib["r"])
            row_map: dict[int, Any] = {}
            for c in row.findall("a:c", ns):
                ref = c.attrib.get("r", "")
                col = _col_num(ref)
                ctype = c.attrib.get("t")
                if ctype == "inlineStr":
                    node = c.find("a:is/a:t", ns)
                    value = node.text if node is not None else ""
                else:
                    vnode = c.find("a:v", ns)
                    if vnode is None:
                        value = None
                    else:
                        raw = vnode.text or ""
                        if ctype == "s":
                            try:
                                value = shared[int(raw)]
                            except Exception:
                                value = raw
                        elif ctype == "b":
                            value = raw == "1"
                        else:
                            try:
                                value = int(raw) if re.fullmatch(r"-?\d+", raw) else float(raw)
                            except Exception:
                                value = raw
                row_map[col] = value
            out[rnum] = row_map
        return out


def _read_with_calamine(path: str | Path, sheet_name: str) -> dict[int, dict[int, Any]]:
    import pandas as pd

    df = pd.read_excel(path, sheet_name=sheet_name, header=None, engine="calamine")
    out: dict[int, dict[int, Any]] = {}
    for ri, row in df.iterrows():
        vals: dict[int, Any] = {}
        for ci, value in enumerate(row.tolist(), start=1):
            try:
                is_na = pd.isna(value)
            except Exception:
                is_na = False
            if not is_na:
                vals[ci] = value
        out[int(ri) + 1] = vals
    return out


def read_sheet(path: str | Path, sheet_name: str) -> dict[int, dict[int, Any]]:
    suffix = Path(path).suffix.lower()
    if suffix in {".xlsx", ".xlsm"}:
        try:
            return _read_ooxml_sheet(path, sheet_name)
        except Exception:
            return _read_with_calamine(path, sheet_name)
    return _read_with_calamine(path, sheet_name)


def parse_timetable_file(path: str | Path) -> dict[str, Any]:
    """Parse the school's V4.6 timetable workbook into normalized teacher-period rows."""
    teacher_rows = read_sheet(path, "ข้อมูลครูผู้สอน")
    schedule_rows = read_sheet(path, "ลงตาราง")

    teachers: list[str] = []
    for r in range(5, 60):
        name = normalize_teacher(teacher_rows.get(r, {}).get(2))
        if name and name.startswith("ครู") and name not in teachers:
            teachers.append(name)
    teacher_set = set(teachers)

    entries: list[dict[str, Any]] = []
    warnings: list[str] = []
    current_day = ""
    seen = set()

    for r in sorted(schedule_rows):
        row = schedule_rows.get(r, {})
        day_candidate = clean_text(row.get(2))
        if day_candidate in DAY_SET:
            current_day = day_candidate

        class_name = clean_text(row.get(3))
        if not current_day or not class_name or not re.match(r"^[ปม]\.\d+$", class_name):
            continue

        # In this workbook each class uses 3 rows: subject, teacher 1, teacher 2/class marker.
        next1 = schedule_rows.get(r + 1, {})
        next2 = schedule_rows.get(r + 2, {})
        has_subject = any(clean_text(row.get(c)) for c in range(4, 12))
        if not has_subject:
            continue

        for col in range(4, 12):
            period = col - 3
            subject = clean_text(row.get(col))
            if not subject:
                continue
            t1 = normalize_teacher(next1.get(col))
            t2 = normalize_teacher(next2.get(col))
            candidates: list[tuple[int, str]] = []
            if t1 and (t1 in teacher_set or t1.startswith("ครู")):
                candidates.append((1, t1))
            if t2 and (t2 in teacher_set or t2.startswith("ครู")):
                candidates.append((2, t2))
            if not candidates:
                warnings.append(f"{current_day} {class_name} คาบ {period} ({subject}) ไม่พบชื่อครูจากค่าที่บันทึกไว้ในไฟล์")
                continue
            for slot, teacher in candidates:
                key = (current_day, period, class_name, subject, teacher)
                if key in seen:
                    continue
                seen.add(key)
                entries.append({
                    "weekday": current_day,
                    "period": period,
                    "class_name": class_name,
                    "subject": subject,
                    "teacher": teacher,
                    "teacher_slot": slot,
                })
                if teacher not in teacher_set:
                    teachers.append(teacher)
                    teacher_set.add(teacher)

    if not entries:
        raise ValueError("อ่านชีต 'ลงตาราง' ได้ แต่ไม่พบรายการ วัน/คาบ/ชั้น/ครู ที่นำมาใช้ได้")

    missing_days = [d for d in DAYS_TH if d not in {e["weekday"] for e in entries}]
    if missing_days:
        warnings.append("ไม่พบข้อมูลวัน: " + ", ".join(missing_days))

    return {
        "teachers": teachers,
        "entries": entries,
        "warnings": warnings,
        "stats": {
            "teachers": len(teachers),
            "entries": len(entries),
            "days": len({e["weekday"] for e in entries}),
            "classes": len({e["class_name"] for e in entries}),
        },
    }
