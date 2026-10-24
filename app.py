from __future__ import annotations

import os
import shutil
import tempfile
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import streamlit as st


def _load_secrets_into_env() -> None:
    """Copy Streamlit Secrets into environment variables BEFORE database.py is imported.

    database.py reads DATABASE_URL etc. via os.getenv() at import time. Streamlit Community Cloud
    already exports root-level secrets as env vars, but a local run with .streamlit/secrets.toml
    does not — without this, a local run silently falls back to an empty SQLite file.
    """
    try:
        for key, value in st.secrets.items():
            if isinstance(value, (str, int, float, bool)) and not os.getenv(str(key)):
                os.environ[str(key)] = str(value)
    except Exception:
        pass  # no secrets.toml → use real env vars / local SQLite


_load_secrets_into_env()

from database import (  # noqa: E402
    COLS, ROLE_LABELS, academic_year_bounds, audit_df, authenticate_user, change_own_password,
    clear_sample_records, create_user, database_kind, delete_records, delete_import_batches, df_all, get_settings,
    has_permission, imported_files_df, init_db, insert_records, is_file_imported, list_users_df,
    make_portable_backup_bytes, migrate_v3_payload, read_v3_sqlite_bytes, reset_user_password,
    restore_portable_backup_bytes, safe_database_url, set_settings, term_bounds, thai_academic_year,
    update_user, today_th, timetable_df, timetable_imports_df, import_timetable,
    recommend_substitutes, add_unavailability, delete_unavailability, unavailability_df,
    teacher_status_df, inactive_teachers, set_teacher_active, delete_teacher_current_data,
)
from parser_engine import file_sha256, libreoffice_status, parse_document, tesseract_status  # noqa: E402
from report_engine import docx_bytes_to_pdf, make_excel, make_substitution_docx, thai_date_long  # noqa: E402
from storage import load_file, storage_status, store_file  # noqa: E402
from timetable_engine import parse_timetable_file  # noqa: E402

APP_VERSION = "4.2.5 Easy Open"
THAI_MONTHS = {
    0: "ทุกเดือน", 1: "มกราคม", 2: "กุมภาพันธ์", 3: "มีนาคม", 4: "เมษายน",
    5: "พฤษภาคม", 6: "มิถุนายน", 7: "กรกฎาคม", 8: "สิงหาคม",
    9: "กันยายน", 10: "ตุลาคม", 11: "พฤศจิกายน", 12: "ธันวาคม",
}
DAY_NAMES = ["จันทร์", "อังคาร", "พุธ", "พฤหัสบดี", "ศุกร์", "เสาร์", "อาทิตย์"]


@st.cache_data(ttl=600, show_spinner=False)
def cached_timetable_df(academic_year: int | None = None, term: int | None = None) -> pd.DataFrame:
    """Cache master timetable data to avoid repeated Supabase round-trips on widget reruns."""
    return timetable_df(academic_year, term)


@st.cache_data(ttl=600, show_spinner=False)
def cached_teacher_choices() -> list[str]:
    tt = timetable_df()
    if tt.empty:
        return []
    inactive = inactive_teachers()
    return sorted(
        x for x in tt["teacher"].dropna().astype(str).str.strip().unique()
        if x and x not in inactive and "ลส.-" not in x and "สำรอง" not in x
    )


@st.cache_data(ttl=60, show_spinner=False)
def cached_recommend_substitutes(
    date_iso: str, academic_year: int, term: int, period: int, absent_teacher: str, subject: str
) -> pd.DataFrame:
    return recommend_substitutes(
        date_iso, int(academic_year), int(term), int(period), absent_teacher, subject, True
    )


@st.cache_data(ttl=60, show_spinner=False)
def cached_unavailability_df(date_iso: str) -> pd.DataFrame:
    return unavailability_df(date_iso)


def clear_assist_caches(*, timetable: bool = False) -> None:
    """Invalidate only caches affected by a write."""
    cached_recommend_substitutes.clear()
    cached_unavailability_df.clear()
    if timetable:
        cached_timetable_df.clear()
        cached_teacher_choices.clear()


def normalize_record(r: dict) -> dict:
    out = dict(r)
    raw_date = str(out.get("date_iso", "")).strip()
    try:
        d = date.fromisoformat(raw_date[:10])
        out["date_iso"] = d.isoformat()
        out["day_name"] = DAY_NAMES[d.weekday()]
        out["thai_year"] = d.year + 543
    except Exception:
        out["date_iso"] = ""
        out["day_name"] = ""
        out["thai_year"] = 0
    try:
        out["period"] = int(float(out.get("period") or 0))
    except Exception:
        out["period"] = 0
    for c in [
        "absent_teacher", "leave_type", "subject", "class_name", "substitute_teacher",
        "acknowledged", "note", "source_file", "confidence", "import_method",
    ]:
        out[c] = str(out.get(c, "") or "").strip()
    return out


def report_filter(df: pd.DataFrame, settings: dict[str, str]):
    if df.empty:
        return df, {}
    work = df.copy()
    work["_date"] = pd.to_datetime(work["date_iso"], errors="coerce").dt.date
    work = work[work["_date"].notna()].copy()
    work["_academic_year"] = work["_date"].map(
        lambda d: thai_academic_year(d, settings.get("academic_year_start", "05-01"))
    )
    years = sorted([int(x) for x in work["_academic_year"].dropna().unique()], reverse=True)
    if not years:
        years = [today_th().year + 543]

    c1, c2, c3 = st.columns(3)
    ay = c1.selectbox("ปีการศึกษา", years, index=0)
    term_label = c2.selectbox("ภาคเรียน", ["ทั้งหมด", "ภาคเรียนที่ 1", "ภาคเรียนที่ 2"])
    month = c3.selectbox("เดือน", list(THAI_MONTHS.keys()), format_func=lambda x: THAI_MONTHS[x])

    start, end = academic_year_bounds(ay, settings)
    if term_label == "ภาคเรียนที่ 1":
        start, end = term_bounds(ay, 1, settings)
    elif term_label == "ภาคเรียนที่ 2":
        start, end = term_bounds(ay, 2, settings)

    f = work[(work["_date"] >= start) & (work["_date"] <= end)].copy()
    if month:
        f = f[f["_date"].map(lambda d: d.month == month)]

    c4, c5 = st.columns(2)
    subs = ["ทั้งหมด"] + sorted(x for x in f["substitute_teacher"].dropna().astype(str).unique() if x.strip())
    absents = ["ทั้งหมด"] + sorted(x for x in f["absent_teacher"].dropna().astype(str).unique() if x.strip())
    sub = c4.selectbox("ผู้สอนแทน", subs)
    absent = c5.selectbox("ครูประจำวิชา/คนลา", absents)
    if sub != "ทั้งหมด":
        f = f[f["substitute_teacher"] == sub]
    if absent != "ทั้งหมด":
        f = f[f["absent_teacher"] == absent]
    return f, {"academic_year": ay, "term": term_label, "month": month, "start": start, "end": end}


def login_screen() -> None:
    settings = get_settings()
    st.title(f"📚 {settings.get('app_title', 'ระบบนับและสรุปการสอนแทน')}")
    st.caption("V4.2 Timetable Assist — Streamlit Community Cloud / Supabase PostgreSQL / Word-PDF-OCR")
    left, center, right = st.columns([1, 1.2, 1])
    with center:
        with st.container(border=True):
            st.subheader("เข้าสู่ระบบ")
            with st.form("login_form"):
                username = st.text_input("ชื่อผู้ใช้")
                password = st.text_input("รหัสผ่าน", type="password")
                ok = st.form_submit_button("เข้าสู่ระบบ", type="primary", width="stretch")
            if ok:
                user = authenticate_user(username, password)
                if user:
                    st.session_state.user = user
                    st.rerun()
                else:
                    st.error("ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง")
            st.caption("บัญชี Admin ครั้งแรกกำหนดผ่าน Streamlit Secrets และระบบจะบังคับเปลี่ยนรหัสผ่านหลังเข้าสู่ระบบ")


def require_permission(permission: str) -> None:
    role = st.session_state.user.get("role", "viewer")
    if not has_permission(role, permission):
        st.error("บัญชีนี้ไม่มีสิทธิ์ใช้งานส่วนนี้")
        st.stop()


st.set_page_config(page_title="ระบบนับการสอนแทน V4.2", page_icon="📚", layout="wide")
try:
    init_db()
except Exception as exc:
    msg = str(exc)
    low = msg.lower()
    st.error("ยังเชื่อมต่อ/ตั้งค่าฐานข้อมูลไม่สำเร็จ")
    if "bootstrap_admin_password" in low:
        st.info("ยังไม่ได้ตั้งรหัส Admin: ไปที่ Streamlit → App settings → Secrets แล้วเพิ่ม BOOTSTRAP_ADMIN_PASSWORD")
    elif "password authentication failed" in low:
        st.info("รหัสผ่านฐานข้อมูลใน DATABASE_URL ไม่ถูกต้อง — ตรวจรหัสใน Streamlit Secrets ให้ตรงกับ Database Password ของ Supabase")
    else:
        st.warning(
            "**สาเหตุที่พบบ่อยที่สุด: Supabase (ฐานข้อมูลฟรี) ถูกพักไว้ เพราะไม่มีคนใช้เกิน 7 วัน**\n\n"
            "วิธีแก้ (ข้อมูลไม่หาย):\n"
            "1. เข้า https://supabase.com/dashboard แล้ว Login\n"
            "2. คลิกโปรเจกต์ของระบบนี้ → กดปุ่ม **Resume project** / **Restore project**\n"
            "3. รอ 2–5 นาทีจนสถานะเป็นสีเขียว แล้วกลับมากด **Rerun** หรือรีเฟรชหน้านี้\n\n"
            "ถ้ายังไม่ได้: ตรวจ DATABASE_URL ใน Streamlit → App settings → Secrets "
            "(ใช้ Session pooler พอร์ต 5432 และมี ?sslmode=require)"
        )
        if st.button("ลองเชื่อมต่อใหม่", type="primary"):
            st.rerun()
    with st.expander("รายละเอียดข้อผิดพลาด (สำหรับผู้ดูแล)"):
        st.code(msg)
    st.stop()
st.markdown(
    """
    <style>
      .block-container {padding-top: 1.25rem; padding-bottom: 2rem;}
      div[data-testid="stMetric"] {border:1px solid #dfe7ef; border-radius:12px; padding:10px 14px;}
      .online-badge {display:inline-block;padding:4px 10px;border-radius:999px;background:#e7f5ee;color:#146c43;font-size:.85rem;font-weight:600}
    </style>
    """,
    unsafe_allow_html=True,
)

for key, default in {
    "user": None,
    "batch_records": [], "batch_file_hashes": {}, "batch_warnings": {}, "batch_file_bytes": {},
    "last_pdf": None, "timetable_preview": None, "timetable_preview_meta": None,
}.items():
    if key not in st.session_state:
        st.session_state[key] = default

if not st.session_state.user:
    login_screen()
    st.stop()

user = st.session_state.user
username = str(user.get("username", ""))
role = str(user.get("role", "viewer"))

if bool(user.get("must_change_password")):
    st.title("🔐 เปลี่ยนรหัสผ่านก่อนเริ่มใช้งาน")
    st.warning("บัญชีนี้ใช้รหัสผ่านชั่วคราว กรุณาเปลี่ยนก่อนใช้งานระบบ")
    with st.form("force_pw"):
        old = st.text_input("รหัสผ่านปัจจุบัน", type="password")
        new1 = st.text_input("รหัสผ่านใหม่ (อย่างน้อย 8 ตัวอักษร)", type="password")
        new2 = st.text_input("ยืนยันรหัสผ่านใหม่", type="password")
        ok = st.form_submit_button("เปลี่ยนรหัสผ่าน", type="primary")
    if ok:
        if new1 != new2:
            st.error("รหัสผ่านใหม่ไม่ตรงกัน")
        else:
            try:
                change_own_password(username, old, new1)
                fresh = authenticate_user(username, new1)
                st.session_state.user = fresh
                st.success("เปลี่ยนรหัสผ่านแล้ว")
                st.rerun()
            except Exception as exc:
                st.error(str(exc))
    st.stop()

settings = get_settings()
st.title(f"📚 {settings.get('app_title', 'ระบบนับและสรุปการสอนแทน')}")
st.caption(f"เวอร์ชัน {APP_VERSION} | ฐานข้อมูล {database_kind()} | ไม่ใช้ Generative AI ในการอ่านเอกสาร")

menu = ["แดชบอร์ด", "ช่วยจัดครูสอนแทน", "รายงาน / พิมพ์", "ข้อมูลทั้งหมด", "นำเข้า / ส่งออก", "บัญชีของฉัน"]
if has_permission(role, "upload"):
    menu.insert(0, "อัปโหลด Word/PDF")
if has_permission(role, "manual"):
    menu.insert(menu.index("ข้อมูลทั้งหมด"), "บันทึกด้วยมือ")
if has_permission(role, "settings"):
    menu.append("ตั้งค่า / ระบบ")
if has_permission(role, "users"):
    menu.append("ผู้ใช้งาน / Audit")

with st.sidebar:
    st.markdown("<span class='online-badge'>● FreeCloud V4.2</span>", unsafe_allow_html=True)
    st.subheader(user.get("display_name") or username)
    st.caption(f"{ROLE_LABELS.get(role, role)} · @{username}")
    page = st.radio("เมนู", menu, label_visibility="collapsed")
    st.divider()
    if st.button("ออกจากระบบ", width="stretch"):
        st.session_state.user = None
        st.rerun()
    st.caption("Word / PDF / PDF สแกน OCR · Supabase PostgreSQL · Multi-user")


if page == "อัปโหลด Word/PDF":
    require_permission("upload")
    st.subheader("📥 เพิ่มเอกสาร Word / PDF")
    st.write("อัปโหลดได้หลายไฟล์ ระบบอ่านข้อมูลและให้ตรวจ Preview ก่อนบันทึกฐานข้อมูลกลาง")

    ocr_status = tesseract_status()
    c1, c2 = st.columns([2, 1])
    with c1:
        uploaded_files = st.file_uploader(
            "เลือกเอกสาร", type=["doc", "docx", "pdf"], accept_multiple_files=True,
            help=".doc ใช้ LibreOffice / PDF สแกนใช้ Tesseract OCR ภาษาไทย",
        )
    with c2:
        use_ocr = st.checkbox("OCR อัตโนมัติเมื่อ PDF อ่านตารางไม่ได้", value=True)
        force_ocr = st.checkbox("บังคับ OCR PDF ทุกไฟล์", value=False)
        if use_ocr and not ocr_status["available"]:
            st.warning("เซิร์ฟเวอร์ยังไม่พบ Tesseract — PDF สแกนจะยังอ่านไม่ได้")
        elif use_ocr and not ocr_status["thai"]:
            st.warning("Tesseract ยังไม่มีภาษาไทย (tha)")

    b1, b2 = st.columns(2)
    read_btn = b1.button("1) อ่านไฟล์ทั้งหมด", type="primary", disabled=not uploaded_files)
    clear_btn = b2.button("ล้างรายการที่อ่านแล้ว")
    if clear_btn:
        for k in ["batch_records", "batch_file_hashes", "batch_warnings", "batch_file_bytes"]:
            st.session_state[k] = {} if k != "batch_records" else []
        st.rerun()

    if read_btn and uploaded_files:
        all_records, file_hashes, warnings_map, file_bytes = [], {}, {}, {}
        name_counts = {}
        progress = st.progress(0, text="กำลังอ่านเอกสาร...")
        for i, uploaded in enumerate(uploaded_files, start=1):
            base_name = uploaded.name
            name_counts[base_name] = name_counts.get(base_name, 0) + 1
            display_name = base_name if name_counts[base_name] == 1 else f"{base_name} ({name_counts[base_name]})"
            suffix = Path(base_name).suffix.lower()
            raw_bytes = uploaded.getvalue()
            file_bytes[display_name] = raw_bytes
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp.write(raw_bytes)
                tmp_path = tmp.name
            try:
                fh = file_sha256(tmp_path)
                file_hashes[display_name] = fh
                already = is_file_imported(fh)
                warnings = []
                if already:
                    warnings.append(f"ไฟล์นี้เคยนำเข้าแล้ว: {already[0]} เมื่อ {already[1]} ({already[2]} รายการ)")
                recs, parse_warnings, converted = parse_document(
                    tmp_path, enable_ocr=use_ocr,
                    force_ocr=(force_ocr and suffix == ".pdf"),
                )
                warnings.extend(parse_warnings)
                if converted is not None:
                    try:
                        shutil.rmtree(converted.parent, ignore_errors=True)
                    except Exception:
                        pass
                for r in recs:
                    r["source_file"] = display_name
                    all_records.append(normalize_record(r))
                warnings_map[display_name] = warnings
            except Exception as exc:
                warnings_map[display_name] = [f"อ่านไฟล์ไม่สำเร็จ: {exc}"]
            finally:
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass
            progress.progress(i / len(uploaded_files), text=f"อ่านแล้ว {i}/{len(uploaded_files)} ไฟล์")
        progress.empty()
        st.session_state.batch_records = all_records
        st.session_state.batch_file_hashes = file_hashes
        st.session_state.batch_warnings = warnings_map
        st.session_state.batch_file_bytes = file_bytes

    if st.session_state.batch_warnings:
        with st.expander("ผลการตรวจไฟล์ / คำเตือน", expanded=True):
            for fname, warnings in st.session_state.batch_warnings.items():
                if warnings:
                    st.markdown(f"**{fname}**")
                    for w in warnings:
                        st.warning(w)

    if st.session_state.batch_records:
        st.markdown("### 2) ตรวจสอบข้อมูลก่อนนำเข้า")
        preview = pd.DataFrame(st.session_state.batch_records)
        show_cols = [
            "source_file", "date_iso", "absent_teacher", "leave_type", "period", "subject",
            "class_name", "substitute_teacher", "acknowledged", "note", "confidence",
        ]
        for c in show_cols:
            if c not in preview.columns:
                preview[c] = ""
        edited = st.data_editor(
            preview[show_cols], width="stretch", hide_index=True, num_rows="fixed",
            disabled=["source_file", "confidence"],
            column_config={
                "source_file": "ไฟล์", "date_iso": "วันที่ (YYYY-MM-DD)", "absent_teacher": "ครูประจำวิชา/คนลา",
                "leave_type": st.column_config.SelectboxColumn("ประเภทการลา", options=["ลากิจ", "ลาป่วย", "ไปราชการ", "ลาคลอด", "ไม่ระบุ"]),
                "period": st.column_config.NumberColumn("คาบ", min_value=1, max_value=20, step=1),
                "subject": "วิชา", "class_name": "ชั้น", "substitute_teacher": "ผู้สอนแทน",
                "acknowledged": "รับทราบ", "note": "หมายเหตุ", "confidence": "ความมั่นใจ",
            },
        )
        ocr_count = edited["confidence"].astype(str).str.contains("OCR", na=False).sum()
        c1, c2, c3 = st.columns(3)
        c1.metric("รายการที่พบ", len(edited))
        c2.metric("มาจาก OCR", int(ocr_count))
        c3.metric("ไฟล์", edited["source_file"].nunique())
        if ocr_count:
            st.warning("รายการจาก OCR ควรตรวจชื่อครู วันที่ วิชา ชั้น และผู้สอนแทนก่อนยืนยัน")

        if st.button("3) ยืนยันนำเข้าฐานข้อมูลกลาง", type="primary"):
            normalized = [normalize_record(x) for x in edited.to_dict("records")]
            total_added = total_skipped = 0
            storage_errors = []
            for fname, group in pd.DataFrame(normalized).groupby("source_file", dropna=False):
                fname = str(fname)
                fh = st.session_state.batch_file_hashes.get(fname, f"edited-{fname}")
                recs = group.to_dict("records")
                method_set = sorted(set(str(r.get("import_method", "")) for r in recs if r.get("import_method")))
                method = ",".join(method_set) or "document"
                warning_text = " | ".join(st.session_state.batch_warnings.get(fname, []))
                storage_key = ""
                raw = st.session_state.batch_file_bytes.get(fname)
                if raw:
                    try:
                        storage_key = store_file(raw, fname, fh)
                    except Exception as exc:
                        storage_errors.append(f"{fname}: เก็บไฟล์ต้นฉบับไม่สำเร็จ ({exc}) แต่ข้อมูลตารางยังนำเข้าได้")
                added, skipped = insert_records(
                    recs, fh, fname, method, warning_text, storage_key=storage_key, username=username,
                )
                total_added += added
                total_skipped += skipped
            st.success(f"นำเข้าเรียบร้อย {total_added} รายการ | ข้ามรายการซ้ำ/ข้อมูลไม่ครบ {total_skipped} รายการ")
            for msg in storage_errors:
                st.warning(msg)
            st.session_state.batch_records = []
            st.session_state.batch_file_hashes = {}
            st.session_state.batch_warnings = {}
            st.session_state.batch_file_bytes = {}


elif page == "ช่วยจัดครูสอนแทน":
    st.subheader("🧑‍🏫 ช่วยจัดครูสอนแทน")
    st.caption("เวอร์ชัน 4.2.1 ลดอาการหน้าจอซีดจาก dropdown: เลือกข้อมูลในฟอร์มให้ครบ แล้วค่อยสั่งค้นหา")

    tab_help, tab_import, tab_unavailable, tab_teacher, tab_view = st.tabs([
        "ช่วยเลือกครู", "นำเข้าตารางสอน", "ครูไม่ว่าง/ติดภารกิจ", "จัดการครู", "ดูตารางสอน"
    ])

    with tab_help:
        # Step 1: widgets live inside a form, so changing dropdowns does NOT rerun the whole page.
        teacher_choices = cached_teacher_choices()
        with st.form("assist_step1", clear_on_submit=False):
            c1, c2, c3 = st.columns(3)
            work_date = c1.date_input("วันที่ต้องจัดสอนแทน", value=today_th(), key="assist_date_v421")
            default_ay = thai_academic_year(work_date, settings.get("academic_year_start", "05-01"))
            ay = c2.number_input("ปีการศึกษา (พ.ศ.)", min_value=2500, max_value=2700, value=int(default_ay), step=1, key="assist_ay_v421")
            term = c3.selectbox("ภาคเรียน", [1, 2], key="assist_term_v421", format_func=lambda x: f"ภาคเรียนที่ {x}")
            absent = st.selectbox("ครูที่ไม่สามารถเข้าสอนได้", [""] + teacher_choices, key="assist_absent_v421")
            find_periods = st.form_submit_button("1) ค้นหาคาบของครู", type="primary", width="stretch")

        if find_periods:
            st.session_state.pop("assist_ranked_v421", None)
            st.session_state.pop("assist_selected_v421", None)
            if not absent:
                st.warning("กรุณาเลือกครูที่ไม่สามารถเข้าสอนได้")
                st.session_state.pop("assist_query_v421", None)
                st.session_state.pop("assist_own_v421", None)
            else:
                tt = cached_timetable_df(int(ay), int(term))
                if tt.empty:
                    st.info("ยังไม่มีตารางสอนสำหรับปีการศึกษา/ภาคเรียนนี้ กรุณาเปิดแท็บ 'นำเข้าตารางสอน' ก่อน")
                    st.session_state.pop("assist_query_v421", None)
                    st.session_state.pop("assist_own_v421", None)
                else:
                    weekday = DAY_NAMES[work_date.weekday()]
                    day_tt = tt[tt["weekday"].astype(str) == weekday].copy()
                    own = day_tt[day_tt["teacher"].astype(str).str.strip() == absent].copy()
                    own_rows = []
                    if not own.empty:
                        own = own.sort_values(["period", "class_name", "subject"])
                        for _, r in own.iterrows():
                            own_rows.append({
                                "label": f"คาบ {int(r['period'])} | {r['subject']} | {r['class_name']}",
                                "period": int(r["period"]),
                                "subject": str(r["subject"]),
                                "class_name": str(r["class_name"]),
                            })
                    st.session_state["assist_query_v421"] = {
                        "date_iso": work_date.isoformat(), "weekday": weekday,
                        "academic_year": int(ay), "term": int(term), "absent": absent,
                    }
                    st.session_state["assist_own_v421"] = own_rows

        query = st.session_state.get("assist_query_v421")
        own_rows = st.session_state.get("assist_own_v421")
        if query:
            if date.fromisoformat(query["date_iso"]).weekday() >= 5:
                st.warning("วันที่เลือกเป็นวันเสาร์/อาทิตย์ ตารางต้นฉบับมีเฉพาะวันจันทร์–ศุกร์")
            st.markdown(
                f"**กำลังจัด:** {query['date_iso']} · วัน{query['weekday']} · "
                f"{query['absent']} · ปี {query['academic_year']} / ภาคเรียน {query['term']}"
            )

            # Step 2: period selection is also in a form; dropdown changes do not rerun.
            with st.form("assist_step2", clear_on_submit=False):
                if own_rows:
                    chosen_label = st.selectbox(
                        "คาบของครูที่ต้องจัดสอนแทน",
                        [x["label"] for x in own_rows], key="assist_assignment_v421"
                    )
                    manual_period = None
                    manual_subject = ""
                    manual_class = ""
                else:
                    st.warning(f"ไม่พบคาบสอนของ {query['absent']} ในวัน{query['weekday']} — ระบุคาบเองได้")
                    cc1, cc2, cc3 = st.columns(3)
                    manual_period = int(cc1.number_input("คาบ", min_value=1, max_value=8, value=1, step=1, key="assist_period_manual_v421"))
                    manual_subject = cc2.text_input("วิชา", key="assist_subject_manual_v421")
                    manual_class = cc3.text_input("ชั้น", key="assist_class_manual_v421")
                    chosen_label = ""
                find_subs = st.form_submit_button("2) ค้นหาครูว่าง", type="primary", width="stretch")

            if find_subs:
                if own_rows:
                    chosen = next(x for x in own_rows if x["label"] == chosen_label)
                    selected_period = int(chosen["period"])
                    selected_subject = chosen["subject"]
                    selected_class = chosen["class_name"]
                else:
                    selected_period = int(manual_period)
                    selected_subject = str(manual_subject)
                    selected_class = str(manual_class)
                with st.spinner("กำลังตรวจครูว่าง..."):
                    ranked = cached_recommend_substitutes(
                        query["date_iso"], query["academic_year"], query["term"], selected_period,
                        query["absent"], selected_subject,
                    )
                st.session_state["assist_selected_v421"] = {
                    "period": selected_period, "subject": selected_subject, "class_name": selected_class
                }
                st.session_state["assist_ranked_v421"] = ranked

            selected = st.session_state.get("assist_selected_v421")
            ranked = st.session_state.get("assist_ranked_v421")
            if selected is not None and isinstance(ranked, pd.DataFrame):
                st.markdown(
                    f"**รายการ:** วัน{query['weekday']} · คาบ {selected['period']} · "
                    f"{selected['subject'] or '-'} · {selected['class_name'] or '-'}"
                )
                if ranked.empty:
                    st.warning("ยังไม่พบข้อมูลครูสำหรับจัดอันดับ")
                else:
                    eligible = ranked[ranked["eligible"] == True].copy()
                    c1, c2, c3 = st.columns(3)
                    c1.metric("ครูว่าง", len(eligible))
                    c2.metric("ครูมีสอน/ไม่ว่าง", len(ranked) - len(eligible))
                    c3.metric("คำแนะนำอันดับ 1", eligible.iloc[0]["teacher"] if not eligible.empty else "ไม่มี")

                    show = ranked.rename(columns={
                        "teacher": "ครู",
                        "status": "สถานะ",
                        "weekly_sub_count": "สอนแทนสัปดาห์นี้",
                        "daily_sub_count": "สอนแทนวันนี้",
                        "monthly_sub_count": "สอนแทนเดือนนี้",
                        "substitute_count": "สอนแทนสะสม",
                        "daily_load": "คาบสอนวันนี้",
                        "subject_match": "ความถนัด",
                        "last_sub_date": "สอนแทนล่าสุด",
                        "fairness_note": "รายละเอียดความเป็นธรรม",
                    })
                    st.dataframe(
                        show[[
                            "ครู", "สถานะ", "สอนแทนสัปดาห์นี้", "สอนแทนวันนี้",
                            "สอนแทนเดือนนี้", "สอนแทนสะสม", "คาบสอนวันนี้",
                            "ความถนัด", "สอนแทนล่าสุด", "รายละเอียดความเป็นธรรม",
                        ]],
                        width="stretch", hide_index=True,
                    )

                    if not eligible.empty:
                        top = eligible.iloc[0]
                        st.info(
                            f"คำแนะนำอันดับ 1: **{top['teacher']}** — "
                            f"สัปดาห์นี้สอนแทน {int(top['weekly_sub_count'])} ครั้ง, "
                            f"วันนี้ {int(top['daily_sub_count'])} ครั้ง, "
                            f"สะสม {int(top['substitute_count'])} ครั้ง, "
                            f"คาบสอนประจำวันนี้ {int(top['daily_load'])} คาบ"
                            + (" และตรงวิชา" if str(top.get('subject_match', '')) else "")
                        )
                        st.caption(
                            "หลักการเรียง: ครูที่สอนแทนน้อยกว่าในสัปดาห์ปัจจุบันจะได้ลำดับก่อน "
                            "จากนั้นจึงพิจารณาจำนวนสอนแทนวันนี้ ยอดสะสม ภาระสอนประจำวันนี้ และความตรงวิชา "
                            "ระบบไม่ตัดสิทธิ์อัตโนมัติเพียงเพราะสอนแทนหลายครั้ง แต่จะลดลำดับและแสดงเหตุผลให้ผู้ใช้ตัดสินใจ"
                        )
                        # Step 3 is a form too; selecting teacher/leave type does not trigger rerun.
                        with st.form("assist_step3", clear_on_submit=False):
                            pick = st.selectbox("เลือกครูผู้สอนแทน", eligible["teacher"].tolist(), key="assist_pick_v421")
                            leave = st.selectbox("ประเภทการลา/เหตุ", ["ลากิจ", "ลาป่วย", "ไปราชการ", "ลาคลอด", "ไม่ระบุ"], key="assist_leave_v421")
                            note = st.text_input("หมายเหตุ", value="ทำใบงาน", key="assist_note_v421")
                            save_ok = st.form_submit_button(
                                "3) ยืนยันและบันทึกการสอนแทน",
                                type="primary", width="stretch",
                                disabled=not has_permission(role, "manual"),
                            )
                        if not has_permission(role, "manual"):
                            st.info("บัญชีนี้ดูคำแนะนำได้ แต่ไม่มีสิทธิ์บันทึกรายการ")
                        elif save_ok:
                            r = normalize_record({
                                "date_iso": query["date_iso"], "absent_teacher": query["absent"], "leave_type": leave,
                                "period": selected["period"], "subject": selected["subject"], "class_name": selected["class_name"],
                                "substitute_teacher": pick, "acknowledged": "", "note": note,
                                "source_file": "ระบบช่วยจัดครูสอนแทน", "confidence": "ตารางสอน", "import_method": "scheduler",
                            })
                            source_hash = f"scheduler-{query['date_iso']}-{query['absent']}-{selected['period']}-{pick}"
                            added, skipped = insert_records([r], source_hash, "ระบบช่วยจัดครูสอนแทน", "scheduler", username=username)
                            if added:
                                clear_assist_caches()
                                st.success("บันทึกการสอนแทนเรียบร้อย")
                                st.session_state.pop("assist_ranked_v421", None)
                                st.rerun()
                            else:
                                st.warning("รายการนี้อาจถูกบันทึกไว้แล้ว หรือข้อมูลสำคัญไม่ครบ")

    with tab_import:
        if not has_permission(role, "timetable"):
            st.info("เฉพาะผู้ดูแลระบบ/ฝ่ายวิชาการเท่านั้นที่นำเข้าตารางสอนได้")
        else:
            st.write("รองรับไฟล์ต้นฉบับรูปแบบ `.xlsm` นี้โดยตรง ระบบอ่านค่าที่บันทึกไว้จากชีต `ข้อมูลครูผู้สอน` และ `ลงตาราง` โดยไม่แกะรหัส VBA")
            c1, c2 = st.columns(2)
            imp_ay = c1.number_input("ปีการศึกษา", min_value=2500, max_value=2700, value=int(thai_academic_year(today_th(), settings.get("academic_year_start", "05-01"))), step=1, key="tt_imp_ay")
            imp_term = c2.selectbox("ภาคเรียน", [1, 2], key="tt_imp_term", format_func=lambda x: f"ภาคเรียนที่ {x}")
            tt_file = st.file_uploader("เลือกไฟล์ตารางสอน", type=["xlsm", "xlsx", "xls"], key="tt_file")
            if tt_file is not None and st.button("1) อ่านและตรวจสอบตารางสอน", type="primary", key="tt_read"):
                raw = tt_file.getvalue()
                suffix = Path(tt_file.name).suffix.lower()
                with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                    tmp.write(raw)
                    tmp_path = tmp.name
                try:
                    parsed = parse_timetable_file(tmp_path)
                    fh = file_sha256(tmp_path)
                    st.session_state.timetable_preview = parsed
                    st.session_state.timetable_preview_meta = {
                        "file_name": tt_file.name, "file_hash": fh, "academic_year": int(imp_ay), "term": int(imp_term)
                    }
                except Exception as exc:
                    st.session_state.timetable_preview = None
                    st.session_state.timetable_preview_meta = None
                    st.error(f"อ่านตารางสอนไม่สำเร็จ: {exc}")
                finally:
                    try:
                        os.unlink(tmp_path)
                    except Exception:
                        pass

            preview = st.session_state.get("timetable_preview")
            meta = st.session_state.get("timetable_preview_meta")
            if preview and meta:
                stats = preview.get("stats", {})
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("ครู", stats.get("teachers", 0))
                c2.metric("รายการครู-คาบ", stats.get("entries", 0))
                c3.metric("วัน", stats.get("days", 0))
                c4.metric("ชั้น", stats.get("classes", 0))
                pdf = pd.DataFrame(preview.get("entries", []))
                if not pdf.empty:
                    display = pdf.rename(columns={"weekday":"วัน", "period":"คาบ", "class_name":"ชั้น", "subject":"วิชา", "teacher":"ครู", "teacher_slot":"ครูคนที่"})
                    st.dataframe(display[["วัน", "คาบ", "ชั้น", "วิชา", "ครู", "ครูคนที่"]].head(300), width="stretch", hide_index=True)
                warnings = preview.get("warnings", [])
                if warnings:
                    with st.expander(f"คำเตือน {len(warnings)} รายการ"):
                        for w in warnings[:100]:
                            st.warning(w)
                st.warning(f"เมื่อยืนยัน ระบบจะแทนที่ตารางสอนเดิมของปีการศึกษา {meta['academic_year']} ภาคเรียนที่ {meta['term']} เท่านั้น")
                if st.button("2) ยืนยันนำเข้าตารางสอน", type="primary", key="tt_confirm"):
                    n = import_timetable(
                        preview.get("entries", []), meta["academic_year"], meta["term"], meta["file_hash"], meta["file_name"],
                        teacher_count=stats.get("teachers", 0), warning_text=" | ".join(warnings), username=username,
                    )
                    clear_assist_caches(timetable=True)
                    st.success(f"นำเข้าตารางสอนแล้ว {n} รายการ")
                    st.session_state.timetable_preview = None
                    st.session_state.timetable_preview_meta = None
                    st.rerun()

            hist = timetable_imports_df()
            if not hist.empty:
                st.markdown("#### ประวัตินำเข้าตารางสอน")
                st.dataframe(hist[[c for c in ["file_name", "academic_year", "term", "row_count", "teacher_count", "imported_at", "uploaded_by"] if c in hist.columns]], width="stretch", hide_index=True)

    with tab_unavailable:
        teacher_choices = cached_teacher_choices()
        d = st.date_input("วันที่", value=today_th(), key="ua_date")
        if has_permission(role, "timetable"):
            with st.form("ua_add"):
                c1, c2, c3 = st.columns(3)
                teacher = c1.selectbox("ครู", teacher_choices, key="ua_teacher") if teacher_choices else c1.text_input("ครู")
                period = c2.selectbox("ช่วงเวลา", [0,1,2,3,4,5,6,7,8], format_func=lambda x: "ทั้งวัน" if x == 0 else f"คาบ {x}")
                reason = c3.text_input("เหตุผล/ภารกิจ")
                add_ok = st.form_submit_button("เพิ่มรายการไม่ว่าง")
            if add_ok:
                add_unavailability(d.isoformat(), teacher, int(period), reason, username=username)
                clear_assist_caches()
                st.success("บันทึกแล้ว")
                st.rerun()
        ua = cached_unavailability_df(d.isoformat())
        if ua.empty:
            st.caption("ยังไม่มีรายการครูไม่ว่าง/ติดภารกิจในวันนี้")
        else:
            show = ua.copy()
            show["ช่วงเวลา"] = show["period"].apply(lambda x: "ทั้งวัน" if int(x or 0) == 0 else f"คาบ {int(x)}")
            st.dataframe(show[["id", "teacher", "ช่วงเวลา", "reason", "created_by"]], width="stretch", hide_index=True)
            if has_permission(role, "timetable"):
                ids = st.multiselect("เลือก ID ที่ต้องการลบ", show["id"].astype(int).tolist(), key="ua_del_ids")
                if st.button("ลบรายการที่เลือก", disabled=not ids, key="ua_del"):
                    delete_unavailability(ids, username=username)
                    clear_assist_caches()
                    st.rerun()

    with tab_teacher:
        st.markdown("#### 👤 จัดการรายชื่อครู")
        st.caption("เหมาะสำหรับกรณีครูย้าย/เกษียณ: แนะนำให้ 'ปิดใช้งาน' เพื่อเก็บประวัติตารางสอนและสอนแทนเดิมไว้")
        if not has_permission(role, "timetable"):
            st.info("เฉพาะผู้ดูแลระบบ/ฝ่ายวิชาการเท่านั้นที่จัดการรายชื่อครูได้")
        else:
            all_tt_m = cached_timetable_df()
            status_df = teacher_status_df()
            inactive_set = inactive_teachers()
            names = set()
            if not all_tt_m.empty:
                names.update(x for x in all_tt_m["teacher"].dropna().astype(str).str.strip().unique() if x)
            if not status_df.empty:
                names.update(x for x in status_df["teacher"].dropna().astype(str).str.strip().unique() if x)
            names = sorted(x for x in names if "ลส.-" not in x and "สำรอง" not in x)

            if not names:
                st.info("ยังไม่มีรายชื่อครูจากตารางสอน")
            else:
                rows = []
                for name in names:
                    active = name not in inactive_set
                    tt_count = int((all_tt_m["teacher"].astype(str).str.strip() == name).sum()) if not all_tt_m.empty else 0
                    note = ""
                    if not status_df.empty:
                        hit = status_df[status_df["teacher"].astype(str).str.strip() == name]
                        if not hit.empty:
                            note = str(hit.iloc[0].get("note") or "")
                    rows.append({"ครู": name, "สถานะ": "ใช้งาน" if active else "ย้าย/ปิดใช้งาน", "รายการในตารางสอน": tt_count, "หมายเหตุ": note})
                st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)

                with st.form("teacher_status_form", clear_on_submit=False):
                    selected_teacher = st.selectbox("เลือกครู", names, key="teacher_manage_name")
                    action = st.radio("การดำเนินการ", ["ปิดใช้งาน (แนะนำสำหรับครูย้าย)", "เปิดใช้งานอีกครั้ง"], horizontal=True)
                    note = st.text_input("หมายเหตุ", placeholder="เช่น ย้ายโรงเรียน 1 ต.ค. 2569")
                    confirm = st.checkbox("ฉันยืนยันการเปลี่ยนสถานะครูรายนี้")
                    status_ok = st.form_submit_button("บันทึกสถานะ", type="primary", disabled=not confirm, width="stretch")
                if status_ok:
                    make_active = action == "เปิดใช้งานอีกครั้ง"
                    set_teacher_active(selected_teacher, make_active, note, username=username)
                    clear_assist_caches(timetable=True)
                    st.success(("เปิดใช้งาน" if make_active else "ปิดใช้งาน") + f" {selected_teacher} เรียบร้อย")
                    st.rerun()

                with st.expander("⚠️ ลบข้อมูลใช้งานของครูแบบถาวร"):
                    st.warning("ใช้เฉพาะเมื่อจำเป็นจริง ๆ: จะลบรายการตารางสอนของครูคนนี้ทั้งหมด และรายการติดภารกิจในอนาคต แต่จะไม่ลบประวัติการสอนแทนเดิม")
                    hard_teacher = st.selectbox("เลือกครูที่จะลบข้อมูลใช้งาน", names, key="teacher_hard_delete_name")
                    hard_text = st.text_input("พิมพ์คำว่า ลบ เพื่อยืนยัน", key="teacher_hard_delete_confirm")
                    if st.button("ลบข้อมูลใช้งานของครู", type="secondary", disabled=(hard_text.strip() != "ลบ"), key="teacher_hard_delete_btn"):
                        result = delete_teacher_current_data(hard_teacher, username=username)
                        set_teacher_active(hard_teacher, False, "ลบข้อมูลใช้งาน/ย้ายออก", username=username)
                        clear_assist_caches(timetable=True)
                        st.success(f"ลบตารางสอน {result['timetable']} รายการ และรายการติดภารกิจในอนาคต {result['unavailability']} รายการแล้ว โดยยังเก็บประวัติการสอนแทนไว้")
                        st.rerun()

    with tab_view:
        all_tt = cached_timetable_df()
        if all_tt.empty:
            st.info("ยังไม่มีตารางสอนในฐานข้อมูล")
        else:
            years = sorted(all_tt["academic_year"].astype(int).unique(), reverse=True)
            c1, c2, c3 = st.columns(3)
            vy = c1.selectbox("ปีการศึกษา", years, key="view_tt_ay")
            terms = sorted(all_tt.loc[all_tt["academic_year"].astype(int) == int(vy), "term"].astype(int).unique())
            vt = c2.selectbox("ภาคเรียน", terms, key="view_tt_term")
            vday = c3.selectbox("วัน", ["ทั้งหมด"] + DAY_NAMES[:5], key="view_tt_day")
            view = cached_timetable_df(int(vy), int(vt))
            if vday != "ทั้งหมด":
                view = view[view["weekday"] == vday]
            show = view.rename(columns={"weekday":"วัน", "period":"คาบ", "class_name":"ชั้น", "subject":"วิชา", "teacher":"ครู"})
            st.dataframe(show[["วัน", "คาบ", "ชั้น", "วิชา", "ครู"]], width="stretch", hide_index=True)


elif page == "แดชบอร์ด":
    st.subheader("📊 แดชบอร์ดสรุปการสอนแทน")
    df = df_all()
    settings = get_settings()
    if df.empty:
        st.info("ยังไม่มีข้อมูล")
    else:
        filtered, meta = report_filter(df, settings)
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("คาบสอนแทนรวม", len(filtered))
        c2.metric("ผู้สอนแทน", filtered["substitute_teacher"].nunique() if not filtered.empty else 0)
        c3.metric("คนลา/ครูประจำวิชา", filtered["absent_teacher"].nunique() if not filtered.empty else 0)
        c4.metric("วันที่มีสอนแทน", filtered["date_iso"].nunique() if not filtered.empty else 0)
        st.caption(f"ช่วงรายงาน: {meta['start'].strftime('%d/%m/%Y')} – {meta['end'].strftime('%d/%m/%Y')}")

        t1, t2, t3, t4 = st.tabs(["ผู้สอนแทน", "คนลา", "วิชา", "แนวโน้มรายเดือน"])
        with t1:
            by_teacher = filtered.groupby("substitute_teacher").size().reset_index(name="จำนวนคาบ").sort_values("จำนวนคาบ", ascending=False) if not filtered.empty else pd.DataFrame()
            st.dataframe(by_teacher, width="stretch", hide_index=True)
            if not by_teacher.empty:
                st.bar_chart(by_teacher.set_index("substitute_teacher"))
        with t2:
            by_absent = filtered.groupby("absent_teacher").size().reset_index(name="จำนวนคาบที่มีผู้สอนแทน").sort_values("จำนวนคาบที่มีผู้สอนแทน", ascending=False) if not filtered.empty else pd.DataFrame()
            st.dataframe(by_absent, width="stretch", hide_index=True)
            if not by_absent.empty:
                st.bar_chart(by_absent.set_index("absent_teacher"))
        with t3:
            by_subject = filtered.groupby("subject").size().reset_index(name="จำนวนคาบ").sort_values("จำนวนคาบ", ascending=False) if not filtered.empty else pd.DataFrame()
            st.dataframe(by_subject, width="stretch", hide_index=True)
        with t4:
            if filtered.empty:
                st.info("ไม่มีข้อมูลในช่วงที่เลือก")
            else:
                trend = filtered.copy()
                trend["เดือน"] = pd.to_datetime(trend["date_iso"]).dt.to_period("M").astype(str)
                trend = trend.groupby("เดือน").size().reset_index(name="จำนวนคาบ")
                st.line_chart(trend.set_index("เดือน"))
                st.dataframe(trend, width="stretch", hide_index=True)

        if has_permission(role, "export"):
            st.download_button(
                "ดาวน์โหลด Excel ตามตัวกรองนี้", data=make_excel(filtered),
                file_name=f"สรุปการสอนแทน_ปีการศึกษา_{meta['academic_year']}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )


elif page == "รายงาน / พิมพ์":
    st.subheader("🖨️ รายงานรายวันและแบบบันทึกการสอนแทน")
    df = df_all()
    settings = get_settings()
    if df.empty:
        st.info("ยังไม่มีข้อมูล")
    else:
        valid_dates = sorted([date.fromisoformat(x) for x in df["date_iso"].dropna().astype(str).unique() if x], reverse=True)
        selected_date = st.selectbox("วันที่", valid_dates, format_func=thai_date_long)
        daydf = df[df["date_iso"] == selected_date.isoformat()].copy()
        absent_list = sorted(x for x in daydf["absent_teacher"].dropna().astype(str).unique() if x.strip())
        if not absent_list:
            st.info("ไม่พบชื่อครูในวันที่เลือก")
        else:
            absent = st.selectbox("ครูประจำวิชา/คนลา", absent_list)
            report_df = daydf[daydf["absent_teacher"] == absent].sort_values("period")
            leave_type = report_df["leave_type"].iloc[0] if not report_df.empty else "ไม่ระบุ"
            c1, c2, c3 = st.columns(3)
            c1.metric("จำนวนคาบ", len(report_df))
            c2.metric("ประเภท", leave_type)
            c3.metric("ผู้สอนแทน", report_df["substitute_teacher"].nunique())
            st.dataframe(
                report_df[["period", "subject", "class_name", "substitute_teacher", "acknowledged", "note"]],
                width="stretch", hide_index=True,
            )
            docx_bytes = make_substitution_docx(report_df, selected_date, absent, settings)
            fname_base = f"บันทึกการสอนแทน_{selected_date.isoformat()}_{absent}".replace("/", "-")
            c1, c2 = st.columns(2)
            c1.download_button(
                "ดาวน์โหลด Word พร้อมพิมพ์", data=docx_bytes, file_name=f"{fname_base}.docx",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document", type="primary",
            )
            if c2.button("สร้าง PDF พร้อมพิมพ์"):
                try:
                    st.session_state.last_pdf = docx_bytes_to_pdf(docx_bytes)
                    st.success("สร้าง PDF แล้ว")
                except Exception as exc:
                    st.session_state.last_pdf = None
                    st.warning(str(exc))
            if st.session_state.last_pdf:
                st.download_button("ดาวน์โหลด PDF", data=st.session_state.last_pdf, file_name=f"{fname_base}.pdf", mime="application/pdf")


elif page == "บันทึกด้วยมือ":
    require_permission("manual")
    st.subheader("✍️ เพิ่มรายการด้วยมือ")
    with st.form("manual"):
        c1, c2, c3 = st.columns(3)
        d = c1.date_input("วันที่", value=today_th())
        absent = c2.text_input("ครูประจำวิชา/คนลา")
        leave = c3.selectbox("ประเภทการลา", ["ลากิจ", "ลาป่วย", "ไปราชการ", "ลาคลอด", "ไม่ระบุ"])
        c4, c5, c6 = st.columns(3)
        period = c4.number_input("คาบ", min_value=1, max_value=20, step=1)
        subject = c5.text_input("วิชา")
        cls = c6.text_input("ชั้น")
        c7, c8 = st.columns(2)
        sub = c7.text_input("ผู้สอนแทน")
        note = c8.text_input("หมายเหตุ", value="ทำใบงาน")
        ok = st.form_submit_button("บันทึก", type="primary")
    if ok:
        r = normalize_record({
            "date_iso": d.isoformat(), "absent_teacher": absent, "leave_type": leave,
            "period": period, "subject": subject, "class_name": cls, "substitute_teacher": sub,
            "acknowledged": "", "note": note, "source_file": "กรอกด้วยมือ", "confidence": "กรอกเอง", "import_method": "manual",
        })
        source_hash = f"manual-{d.isoformat()}-{absent}-{period}"
        added, skipped = insert_records([r], source_hash, "กรอกด้วยมือ", "manual", username=username)
        if added:
            st.success("บันทึกแล้ว")
        else:
            st.warning("รายการซ้ำหรือข้อมูลสำคัญไม่ครบ")


elif page == "ข้อมูลทั้งหมด":
    st.subheader("🗂️ ข้อมูลทั้งหมด")
    df = df_all()
    q = st.text_input("ค้นหา ชื่อครู / วิชา / ชั้น / ชื่อไฟล์")
    if q and not df.empty:
        mask = df.astype(str).apply(lambda col: col.str.contains(q, case=False, na=False)).any(axis=1)
        df = df[mask]
    st.write(f"แสดง {len(df)} รายการ")
    display_cols = [
        "id", "date_iso", "absent_teacher", "leave_type", "period", "subject", "class_name",
        "substitute_teacher", "note", "source_file", "confidence", "created_by", "imported_at",
    ]
    st.dataframe(df[[c for c in display_cols if c in df.columns]], width="stretch", hide_index=True)
    if not df.empty and has_permission(role, "delete"):
        with st.expander("🗑️ ลบข้อมูลแบบยืดหยุ่น", expanded=False):
            st.caption("ลบได้ทั้งบางรายการหรือหลายรายการพร้อมกัน โดยประวัติ Audit Log จะยังคงอยู่")
            del_tab1, del_tab2 = st.tabs(["เลือกลบหลายรายการ", "ลบทั้งหมดที่กำลังแสดง"])

            with del_tab1:
                id_options = df["id"].astype(int).tolist()
                ids = st.multiselect(
                    "เลือก ID ที่ต้องการลบ (เลือกได้หลายรายการ)", id_options, key="bulk_record_ids"
                )
                if ids:
                    preview = df[df["id"].astype(int).isin(ids)]
                    st.dataframe(
                        preview[[c for c in ["id", "date_iso", "absent_teacher", "period", "subject", "class_name", "substitute_teacher", "source_file"] if c in preview.columns]],
                        width="stretch", hide_index=True,
                    )
                confirm = st.checkbox(
                    f"ยืนยันลบ {len(ids)} รายการที่เลือก", value=False, key="bulk_record_confirm"
                )
                if st.button("ลบรายการที่เลือก", disabled=not (ids and confirm), key="bulk_record_delete"):
                    n = delete_records(ids, username=username)
                    cached_records_df.clear() if "cached_records_df" in globals() else None
                    st.success(f"ลบแล้ว {n} รายการ")
                    st.rerun()

            with del_tab2:
                st.warning(
                    f"ขณะนี้ตารางด้านบนกำลังแสดง {len(df)} รายการ หากกดลบทั้งหมด ระบบจะลบเฉพาะผลลัพธ์ที่กำลังแสดงหลังการค้นหา/กรอง ไม่ได้ลบทั้งฐานโดยอัตโนมัติ"
                )
                confirm_text = st.text_input(
                    'พิมพ์คำว่า "ลบทั้งหมด" เพื่อยืนยัน', key="delete_visible_confirm"
                )
                if st.button(
                    f"ลบทั้งหมดที่กำลังแสดง ({len(df)} รายการ)",
                    type="secondary",
                    disabled=(confirm_text.strip() != "ลบทั้งหมด" or df.empty),
                    key="delete_visible_btn",
                ):
                    n = delete_records(df["id"].astype(int).tolist(), username=username)
                    cached_records_df.clear() if "cached_records_df" in globals() else None
                    st.success(f"ลบแล้ว {n} รายการ")
                    st.rerun()


elif page == "นำเข้า / ส่งออก":
    st.subheader("🔄 นำเข้า / ส่งออก / สำรองข้อมูล")
    tabs = ["ส่งออก", "ประวัติไฟล์"]
    if has_permission(role, "upload"):
        tabs.insert(0, "นำเข้า Excel เดิม")
    if has_permission(role, "restore"):
        tabs.extend(["ย้ายจาก V3", "สำรอง/กู้คืน V4"])
    tab_objs = st.tabs(tabs)
    tab_map = dict(zip(tabs, tab_objs))

    if "นำเข้า Excel เดิม" in tab_map:
        with tab_map["นำเข้า Excel เดิม"]:
            old_xlsx = st.file_uploader("เลือกไฟล์ Excel ระบบเดิม", type=["xlsx"], key="old_xlsx")
            if old_xlsx is not None:
                try:
                    raw = pd.read_excel(old_xlsx, sheet_name="บันทึกข้อมูล", engine="calamine")
                    mapping = {
                        "วันที่":"date_iso", "วัน":"day_name", "พ.ศ.":"thai_year", "ครูประจำวิชา/คนลา":"absent_teacher",
                        "ประเภทการลา":"leave_type", "คาบ":"period", "วิชา":"subject", "ชั้น":"class_name",
                        "ผู้สอนแทน":"substitute_teacher", "รับทราบ":"acknowledged", "หมายเหตุ":"note", "ไฟล์อ้างอิง":"source_file",
                    }
                    found = [c for c in mapping if c in raw.columns]
                    if not found:
                        st.error("ไม่พบหัวคอลัมน์รูปแบบระบบเวอร์ชันก่อน")
                    else:
                        old = raw[found].rename(columns=mapping).copy()
                        if "date_iso" in old.columns:
                            old["date_iso"] = pd.to_datetime(old["date_iso"], errors="coerce").dt.date.astype(str)
                            old.loc[old["date_iso"] == "NaT", "date_iso"] = ""
                        for c in COLS:
                            if c not in old.columns:
                                old[c] = ""
                        old["confidence"] = "นำเข้าจาก Excel"
                        old["import_method"] = "excel-migration"
                        old = old[old["substitute_teacher"].astype(str).str.strip().ne("")]
                        st.dataframe(old, width="stretch", hide_index=True)
                        if st.button("ยืนยันนำเข้า Excel", type="primary"):
                            recs = [normalize_record(r) for r in old.fillna("").to_dict("records")]
                            added, skipped = insert_records(
                                recs, f"excel-{old_xlsx.name}-{len(old_xlsx.getvalue())}", old_xlsx.name,
                                "excel-migration", username=username,
                            )
                            st.success(f"นำเข้า {added} รายการ | ข้าม {skipped} รายการ")
                except Exception as exc:
                    st.error(f"อ่าน Excel ไม่สำเร็จ: {exc}")

    with tab_map["ส่งออก"]:
        df = df_all()
        st.write(f"ข้อมูลในฐาน {len(df)} รายการ")
        st.download_button(
            "ดาวน์โหลด Excel ทั้งหมด", make_excel(df), "รายงานการสอนแทน_ทั้งหมด.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    with tab_map["ประวัติไฟล์"]:
        hist = imported_files_df()
        st.dataframe(hist, width="stretch", hide_index=True)

        if not hist.empty and has_permission(role, "delete"):
            with st.expander("🗑️ ลบชุดข้อมูลที่นำเข้าผิด", expanded=False):
                st.info(
                    "เหมาะสำหรับกรณีแนบเอกสาร/ตารางสอนแทนผิด: เลือกไฟล์นำเข้าหนึ่งไฟล์หรือหลายไฟล์ แล้วลบข้อมูลทั้งหมดที่มาจากไฟล์เหล่านั้นในครั้งเดียว"
                )
                hash_choices = hist["file_hash"].astype(str).tolist()
                selected_hashes = st.multiselect(
                    "เลือกไฟล์นำเข้าที่ต้องการลบ (เลือกได้หลายไฟล์)",
                    hash_choices,
                    format_func=lambda h: str(hist.loc[hist["file_hash"].astype(str) == h, "file_name"].iloc[0]),
                    key="delete_import_batches_hashes",
                )
                if selected_hashes:
                    selected_hist = hist[hist["file_hash"].astype(str).isin(selected_hashes)].copy()
                    cols = [c for c in ["file_name", "imported_at", "imported_rows", "import_method", "uploaded_by"] if c in selected_hist.columns]
                    st.dataframe(selected_hist[cols], width="stretch", hide_index=True)
                    expected_rows = int(pd.to_numeric(selected_hist.get("imported_rows", 0), errors="coerce").fillna(0).sum()) if "imported_rows" in selected_hist.columns else 0
                    st.warning(
                        f"กำลังจะลบ {len(selected_hashes)} ชุดนำเข้า (ประวัติระบุรวมประมาณ {expected_rows} รายการ) การลบนี้จะลบ records ที่มาจากไฟล์เหล่านี้และลบรายการในประวัติไฟล์ แต่ Audit Log จะยังอยู่"
                    )
                batch_confirm = st.text_input(
                    'พิมพ์คำว่า "ลบชุดนำเข้า" เพื่อยืนยัน', key="delete_import_batches_confirm"
                )
                if st.button(
                    "ลบชุดนำเข้าที่เลือก",
                    type="secondary",
                    disabled=(not selected_hashes or batch_confirm.strip() != "ลบชุดนำเข้า"),
                    key="delete_import_batches_btn",
                ):
                    result = delete_import_batches(selected_hashes, username=username)
                    st.success(
                        f"ลบแล้ว {result['batches']} ชุดนำเข้า และลบข้อมูลสอนแทนที่เกี่ยวข้อง {result['records']} รายการ"
                    )
                    st.rerun()

        if not hist.empty and "storage_key" in hist.columns:
            candidates = hist[hist["storage_key"].fillna("").astype(str).str.strip().ne("")]
            if not candidates.empty:
                choices = candidates["file_hash"].astype(str).tolist()
                selected_hash = st.selectbox(
                    "ดาวน์โหลดไฟล์ต้นฉบับที่เก็บไว้",
                    choices,
                    format_func=lambda h: str(candidates[candidates["file_hash"] == h]["file_name"].iloc[0]),
                )
                row = candidates[candidates["file_hash"] == selected_hash].iloc[0]
                try:
                    original = load_file(str(row["storage_key"]))
                    if original:
                        st.download_button("ดาวน์โหลดไฟล์ต้นฉบับ", original, str(row["file_name"]), "application/octet-stream")
                    else:
                        st.caption("ไม่พบไฟล์ต้นฉบับใน Storage ปัจจุบัน")
                except Exception as exc:
                    st.warning(f"เปิดไฟล์ต้นฉบับไม่สำเร็จ: {exc}")

    if "ย้ายจาก V3" in tab_map:
        with tab_map["ย้ายจาก V3"]:
            st.write("สำหรับฐานข้อมูล `substitution.db` จาก V3: ระบบจะนำ records และ settings เข้าฐาน V4 โดยไม่ลบข้อมูล V4 เดิม")
            v3db = st.file_uploader("เลือก substitution.db จาก V3", type=["db"], key="v3db")
            if v3db:
                try:
                    payload = read_v3_sqlite_bytes(v3db.getvalue())
                    st.info(f"พบ {len(payload.get('records', []))} รายการใน V3")
                    if st.button("ยืนยันย้าย V3 → V4", type="primary"):
                        result = migrate_v3_payload(payload, username)
                        st.success(f"ย้ายแล้ว {result['added']} รายการ | ข้ามซ้ำ {result['skipped']} รายการ")
                except Exception as exc:
                    st.error(str(exc))

    if "สำรอง/กู้คืน V4" in tab_map:
        with tab_map["สำรอง/กู้คืน V4"]:
            st.download_button(
                "ดาวน์โหลด Portable Backup V4 (.zip)", make_portable_backup_bytes(),
                "substitution_v4_backup.zip", "application/zip", type="primary",
            )
            st.caption("Portable Backup เก็บข้อมูลการสอนแทน ประวัติไฟล์ และการตั้งค่า แต่ไม่ส่งออกรหัสผ่านผู้ใช้")
            backup = st.file_uploader("เลือก substitution_v4_backup.zip เพื่อกู้คืน", type=["zip"], key="restore_v4")
            confirm_restore = st.checkbox("ยืนยัน: การกู้คืนจะล้าง records/imported_files ปัจจุบัน แต่จะไม่ลบบัญชีผู้ใช้")
            if st.button("กู้คืน V4", disabled=not (backup and confirm_restore)):
                try:
                    result = restore_portable_backup_bytes(backup.getvalue(), username)
                    st.success(f"กู้คืนแล้ว {result['records']} รายการ และประวัติไฟล์ {result['files']} รายการ")
                    st.rerun()
                except Exception as exc:
                    st.error(str(exc))


elif page == "ตั้งค่า / ระบบ":
    require_permission("settings")
    st.subheader("⚙️ ตั้งค่าระบบ")
    settings = get_settings()
    tab1, tab2, tab3 = st.tabs(["โรงเรียนและรายงาน", "ปีการศึกษา", "ตรวจระบบ/ออนไลน์"])
    with tab1:
        with st.form("school_settings"):
            title = st.text_input("ชื่อระบบ", settings.get("app_title", "ระบบนับและสรุปการสอนแทน"))
            school = st.text_input("ชื่อโรงเรียน", settings.get("school_name", ""))
            head = st.text_input("หัวหน้าบริหารงานวิชาการ", settings.get("academic_head_name", ""))
            director = st.text_input("ผู้อำนวยการสถานศึกษา", settings.get("director_name", ""))
            periods = st.number_input("จำนวนคาบต่อวัน", 1, 20, int(settings.get("periods_per_day", "6")))
            if st.form_submit_button("บันทึกการตั้งค่า", type="primary"):
                set_settings({
                    "app_title": title, "school_name": school, "academic_head_name": head,
                    "director_name": director, "periods_per_day": periods,
                }, username=username)
                st.success("บันทึกแล้ว")

    with tab2:
        st.write("กำหนดช่วงปีการศึกษาในรูปแบบ เดือน-วัน (MM-DD)")
        with st.form("year_settings"):
            start = st.text_input("เริ่มปีการศึกษา", settings.get("academic_year_start", "05-01"))
            term1_end = st.text_input("สิ้นสุดภาคเรียนที่ 1", settings.get("term1_end", "10-31"))
            term2_start = st.text_input("เริ่มภาคเรียนที่ 2", settings.get("term2_start", "11-01"))
            term2_end = st.text_input("สิ้นสุดภาคเรียนที่ 2", settings.get("term2_end", "04-30"))
            if st.form_submit_button("บันทึกช่วงปีการศึกษา"):
                try:
                    for v in [start, term1_end, term2_start, term2_end]:
                        datetime.strptime(v, "%m-%d")
                    set_settings({
                        "academic_year_start": start, "term1_end": term1_end,
                        "term2_start": term2_start, "term2_end": term2_end,
                    }, username=username)
                    st.success("บันทึกแล้ว")
                except ValueError:
                    st.error("กรุณาใช้รูปแบบ MM-DD เช่น 05-01")

    with tab3:
        ocr = tesseract_status()
        lo_ok, lo_path = libreoffice_status()
        storage = storage_status()
        c1, c2, c3 = st.columns(3)
        with c1:
            st.markdown("**ฐานข้อมูล**")
            if database_kind() == "PostgreSQL":
                st.success("PostgreSQL — เหมาะสำหรับออนไลน์หลายผู้ใช้")
            else:
                st.warning("SQLite — เหมาะสำหรับ Local/Test; Production ควรตั้ง DATABASE_URL เป็น PostgreSQL")
            st.code(safe_database_url())
        with c2:
            st.markdown("**OCR / LibreOffice**")
            if ocr["available"] and ocr["thai"]:
                st.success(f"Tesseract {ocr['version']} + ภาษาไทย พร้อม")
            elif ocr["available"]:
                st.warning("พบ Tesseract แต่ยังไม่มีภาษาไทย (tha)")
            else:
                st.error("ยังไม่พบ Tesseract OCR")
            st.caption(ocr.get("path", ""))
            if lo_ok:
                st.success("LibreOffice พร้อม")
            else:
                st.warning("ยังไม่พบ LibreOffice")
            st.caption(lo_path)
        with c3:
            st.markdown("**Document Storage**")
            if storage["mode"] == "S3":
                st.success(f"S3/Object Storage: {storage.get('bucket')}")
            else:
                st.warning("Local Storage ชั่วคราว — บน Streamlit Cloud ไฟล์ต้นฉบับอาจหายเมื่อแอปรีสตาร์ต แนะนำ Supabase Storage")
            st.caption(storage.get("message", ""))

        st.markdown("#### ข้อมูลตัวอย่าง")
        all_df = df_all()
        sample_count = len(all_df[all_df["source_hash"] == "sample-seed"]) if not all_df.empty else 0
        st.write(f"ข้อมูลตัวอย่างคงเหลือ {sample_count} รายการ")
        confirm_clear = st.checkbox("ยืนยันลบข้อมูลตัวอย่าง 15 คาบ")
        if st.button("ลบข้อมูลตัวอย่าง", disabled=not confirm_clear):
            n = clear_sample_records(username=username)
            st.success(f"ลบข้อมูลตัวอย่างแล้ว {n} รายการ และจะไม่สร้างกลับอัตโนมัติ")
            st.rerun()


elif page == "ผู้ใช้งาน / Audit":
    require_permission("users")
    st.subheader("👥 ผู้ใช้งานและประวัติการทำงาน")
    t1, t2, t3 = st.tabs(["รายชื่อผู้ใช้", "สร้าง/แก้บัญชี", "Audit Log"])
    with t1:
        udf = list_users_df()
        show = udf.copy()
        if not show.empty:
            show["role_name"] = show["role"].map(ROLE_LABELS).fillna(show["role"])
        st.dataframe(show, width="stretch", hide_index=True)

    with t2:
        st.markdown("#### สร้างผู้ใช้ใหม่")
        with st.form("create_user"):
            c1, c2 = st.columns(2)
            new_username = c1.text_input("Username")
            new_name = c2.text_input("ชื่อที่แสดง")
            c3, c4 = st.columns(2)
            new_role = c3.selectbox("สิทธิ์", list(ROLE_LABELS.keys()), format_func=lambda x: ROLE_LABELS[x])
            new_password = c4.text_input("รหัสผ่านชั่วคราว", type="password")
            create_ok = st.form_submit_button("สร้างบัญชี", type="primary")
        if create_ok:
            try:
                create_user(new_username, new_password, new_name, new_role, created_by=username, must_change_password=True)
                st.success("สร้างบัญชีแล้ว ผู้ใช้จะถูกบังคับเปลี่ยนรหัสผ่านเมื่อเข้าใช้ครั้งแรก")
                st.rerun()
            except Exception as exc:
                st.error(str(exc))

        udf = list_users_df()
        if not udf.empty:
            st.markdown("#### แก้บัญชี")
            uid = st.selectbox("เลือกผู้ใช้", udf["id"].astype(int).tolist(), format_func=lambda i: f"{udf[udf['id']==i]['username'].iloc[0]} — {udf[udf['id']==i]['display_name'].iloc[0]}")
            row = udf[udf["id"] == uid].iloc[0]
            with st.form("edit_user"):
                e_name = st.text_input("ชื่อที่แสดง", str(row["display_name"]))
                e_role = st.selectbox("สิทธิ์", list(ROLE_LABELS.keys()), index=list(ROLE_LABELS.keys()).index(str(row["role"])), format_func=lambda x: ROLE_LABELS[x])
                e_active = st.checkbox("เปิดใช้งานบัญชี", value=bool(row["active"]))
                edit_ok = st.form_submit_button("บันทึกบัญชี")
            if edit_ok:
                try:
                    update_user(int(uid), e_name, e_role, e_active, changed_by=username)
                    st.success("บันทึกแล้ว")
                    st.rerun()
                except Exception as exc:
                    st.error(str(exc))

            with st.form("reset_password"):
                reset_pw = st.text_input("รหัสผ่านชั่วคราวใหม่", type="password")
                reset_ok = st.form_submit_button("รีเซ็ตรหัสผ่าน")
            if reset_ok:
                try:
                    reset_user_password(int(uid), reset_pw, changed_by=username, must_change=True)
                    st.success("รีเซ็ตรหัสผ่านแล้ว ผู้ใช้จะต้องเปลี่ยนรหัสผ่านเมื่อเข้าใช้งานครั้งถัดไป")
                except Exception as exc:
                    st.error(str(exc))

    with t3:
        limit = st.selectbox("จำนวนรายการล่าสุด", [100, 500, 1000, 5000], index=1)
        adf = audit_df(limit)
        st.dataframe(adf, width="stretch", hide_index=True)
        if not adf.empty:
            st.download_button("ดาวน์โหลด Audit Log CSV", adf.to_csv(index=False).encode("utf-8-sig"), "audit_log.csv", "text/csv")


elif page == "บัญชีของฉัน":
    st.subheader("🔐 บัญชีของฉัน")
    st.write(f"**ผู้ใช้:** {username}")
    st.write(f"**ชื่อ:** {user.get('display_name', '')}")
    st.write(f"**สิทธิ์:** {ROLE_LABELS.get(role, role)}")
    with st.form("change_my_password"):
        old = st.text_input("รหัสผ่านเดิม", type="password")
        new1 = st.text_input("รหัสผ่านใหม่", type="password")
        new2 = st.text_input("ยืนยันรหัสผ่านใหม่", type="password")
        ok = st.form_submit_button("เปลี่ยนรหัสผ่าน")
    if ok:
        if new1 != new2:
            st.error("รหัสผ่านใหม่ไม่ตรงกัน")
        else:
            try:
                change_own_password(username, old, new1)
                st.success("เปลี่ยนรหัสผ่านเรียบร้อย")
            except Exception as exc:
                st.error(str(exc))
