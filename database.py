from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import secrets
import sqlite3
import tempfile
import zipfile
from datetime import date, datetime
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Iterable, Optional

import pandas as pd
from sqlalchemy import (
    Boolean, Column, Integer, MetaData, String, Table, Text, create_engine, delete,
    insert, select, update,
)
from sqlalchemy.exc import IntegrityError

from parser_engine import record_fingerprint

APP_DIR = Path(__file__).resolve().parent
LOCAL_DB_PATH = APP_DIR / "substitution_v4.db"
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{LOCAL_DB_PATH}")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = "postgresql+psycopg2://" + DATABASE_URL[len("postgres://"):]
elif DATABASE_URL.startswith("postgresql://") and "+" not in DATABASE_URL.split("://", 1)[0]:
    DATABASE_URL = "postgresql+psycopg2://" + DATABASE_URL[len("postgresql://"):]

engine_kwargs = {"pool_pre_ping": True}
if DATABASE_URL.startswith("sqlite:"):
    engine_kwargs["connect_args"] = {"check_same_thread": False}
elif DATABASE_URL.startswith("postgresql"):
    # Keep a small pool: friendly to Supabase Free / shared pooler limits.
    engine_kwargs.update(pool_size=3, max_overflow=2, pool_recycle=300, pool_timeout=15)
    if "supabase.com" in DATABASE_URL and "sslmode=" not in DATABASE_URL:
        engine_kwargs["connect_args"] = {"sslmode": "require", "connect_timeout": 10}
ENGINE = create_engine(DATABASE_URL, future=True, **engine_kwargs)
metadata = MetaData()

records = Table(
    "records", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("date_iso", String(10), nullable=False),
    Column("day_name", String(32), default=""),
    Column("thai_year", Integer, default=0),
    Column("absent_teacher", Text, default=""),
    Column("leave_type", String(64), default=""),
    Column("period", Integer, default=0),
    Column("subject", Text, default=""),
    Column("class_name", String(64), default=""),
    Column("substitute_teacher", Text, default=""),
    Column("acknowledged", String(128), default=""),
    Column("note", Text, default=""),
    Column("source_file", Text, default=""),
    Column("source_hash", String(128), default=""),
    Column("record_hash", String(128), unique=True, nullable=False),
    Column("imported_at", String(40), default=""),
    Column("confidence", String(128), default=""),
    Column("import_method", String(64), default=""),
    Column("created_by", String(128), default=""),
)

imported_files = Table(
    "imported_files", metadata,
    Column("file_hash", String(128), primary_key=True),
    Column("file_name", Text, default=""),
    Column("imported_at", String(40), default=""),
    Column("imported_rows", Integer, default=0),
    Column("import_method", String(64), default=""),
    Column("warning_text", Text, default=""),
    Column("storage_key", Text, default=""),
    Column("uploaded_by", String(128), default=""),
)

settings_table = Table(
    "settings", metadata,
    Column("key", String(128), primary_key=True),
    Column("value", Text, default=""),
)

users = Table(
    "users", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("username", String(128), unique=True, nullable=False),
    Column("password_hash", Text, nullable=False),
    Column("display_name", Text, default=""),
    Column("role", String(32), default="viewer"),
    Column("active", Boolean, default=True),
    Column("created_at", String(40), default=""),
    Column("last_login", String(40), default=""),
    Column("must_change_password", Boolean, default=False),
)

audit_logs = Table(
    "audit_logs", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("occurred_at", String(40), default=""),
    Column("username", String(128), default=""),
    Column("action", String(128), default=""),
    Column("entity_type", String(64), default=""),
    Column("entity_id", String(128), default=""),
    Column("detail", Text, default=""),
)

COLS = [
    "date_iso", "day_name", "thai_year", "absent_teacher", "leave_type", "period",
    "subject", "class_name", "substitute_teacher", "acknowledged", "note", "source_file",
]

SAMPLE_ROWS = [
    ("2026-07-07","อังคาร",2569,"นายวรากร ตรงต่อกิจ","ลากิจ",3,"ภาษาอังกฤษ","ม.1","ครูวีระยุทธ์","","ทำใบงาน","ใบลากิจ 7 - 10 กค.doc"),
    ("2026-07-07","อังคาร",2569,"นายวรากร ตรงต่อกิจ","ลากิจ",4,"ภาษาอังกฤษ","ม.3","ครูวีระยุทธ์","","ทำใบงาน","ใบลากิจ 7 - 10 กค.doc"),
    ("2026-07-07","อังคาร",2569,"นายวรากร ตรงต่อกิจ","ลากิจ",5,"เสริมทักษะภาษาอังกฤษ","ป.5","ครูพศิน","","ทำใบงาน","ใบลากิจ 7 - 10 กค.doc"),
    ("2026-07-08","พุธ",2569,"นายวรากร ตรงต่อกิจ","ลากิจ",1,"ภาษาอังกฤษ","ม.3","ครูวีระยุทธ์","","ทำใบงาน","ใบลากิจ 7 - 10 กค.doc"),
    ("2026-07-08","พุธ",2569,"นายวรากร ตรงต่อกิจ","ลากิจ",2,"ภาษาอังกฤษพื้นฐาน","ป.6","ครูพศิน","","ทำใบงาน","ใบลากิจ 7 - 10 กค.doc"),
    ("2026-07-08","พุธ",2569,"นายวรากร ตรงต่อกิจ","ลากิจ",4,"ภาษาอังกฤษ","ม.1","ครูปิยะพร","","ทำใบงาน","ใบลากิจ 7 - 10 กค.doc"),
    ("2026-07-08","พุธ",2569,"นายวรากร ตรงต่อกิจ","ลากิจ",5,"ภาษาอังกฤษ","ม.2","ครูสุรรัตน์","","ทำใบงาน","ใบลากิจ 7 - 10 กค.doc"),
    ("2026-07-09","พฤหัสบดี",2569,"นายวรากร ตรงต่อกิจ","ลากิจ",1,"เสริมทักษะอังกฤษ","ป.5","ครูอุสา","","ทำใบงาน","ใบลากิจ 7 - 10 กค.doc"),
    ("2026-07-09","พฤหัสบดี",2569,"นายวรากร ตรงต่อกิจ","ลากิจ",2,"ภาษาอังกฤษพื้นฐาน","ป.5","ครูภัคพิชา","","ทำใบงาน","ใบลากิจ 7 - 10 กค.doc"),
    ("2026-07-09","พฤหัสบดี",2569,"นายวรากร ตรงต่อกิจ","ลากิจ",3,"เสริมทักษะอังกฤษ","ป.6","ครูพศิน","","ทำใบงาน","ใบลากิจ 7 - 10 กค.doc"),
    ("2026-07-09","พฤหัสบดี",2569,"นายวรากร ตรงต่อกิจ","ลากิจ",4,"เสริมทักษะอังกฤษ","ป.6","ครูอุสา","","ทำใบงาน","ใบลากิจ 7 - 10 กค.doc"),
    ("2026-07-10","ศุกร์",2569,"นายวรากร ตรงต่อกิจ","ลากิจ",2,"คอมพิวเตอร์","ม.3","ครูวีระยุทธ์","","ทำใบงาน","ใบลากิจ 7 - 10 กค.doc"),
    ("2026-07-10","ศุกร์",2569,"นายวรากร ตรงต่อกิจ","ลากิจ",3,"คอมพิวเตอร์","ม.2","ครูพราภรณ์","","ทำใบงาน","ใบลากิจ 7 - 10 กค.doc"),
    ("2026-07-10","ศุกร์",2569,"นายวรากร ตรงต่อกิจ","ลากิจ",4,"ภาษาอังกฤษ","ม.2","ครูวิวัฒน์","","ทำใบงาน","ใบลากิจ 7 - 10 กค.doc"),
    ("2026-07-10","ศุกร์",2569,"นายวรากร ตรงต่อกิจ","ลากิจ",5,"การป้องกันการทุจริต","ป.5","ครูสุทธวีร์","","ทำใบงาน","ใบลากิจ 7 - 10 กค.doc"),
]

BANGKOK_TZ = ZoneInfo("Asia/Bangkok")

DEFAULT_SETTINGS = {
    "school_name": "โรงเรียนบ้านวังลึก(ยุวนาฏชนูทิศ)",
    "academic_head_name": "นางเจษฎ์สุดา ขำพงศ์",
    "director_name": "นายอัครวีร กระแสร์",
    "academic_year_start": "05-01",
    "term1_end": "10-31",
    "term2_start": "11-01",
    "term2_end": "04-30",
    "periods_per_day": "6",
    "seed_sample": os.getenv("SEED_SAMPLE_DATA", "0"),
    "app_title": "ระบบนับและสรุปการสอนแทน",
}

ROLE_LABELS = {"admin": "ผู้ดูแลระบบ", "academic": "ฝ่ายวิชาการ", "viewer": "ผู้ดูข้อมูล"}
PERMISSIONS = {
    "admin": {"upload", "manual", "delete", "export", "settings", "users", "restore", "view", "report"},
    "academic": {"upload", "manual", "delete", "export", "view", "report"},
    "viewer": {"export", "view", "report"},
}


def now_iso() -> str:
    return datetime.now(BANGKOK_TZ).isoformat(timespec="seconds")


def today_th() -> date:
    return datetime.now(BANGKOK_TZ).date()


def database_kind() -> str:
    return "PostgreSQL" if DATABASE_URL.startswith("postgresql") else "SQLite"


def safe_database_url() -> str:
    if "@" in DATABASE_URL and "://" in DATABASE_URL:
        prefix, rest = DATABASE_URL.split("://", 1)
        if "@" in rest:
            creds, host = rest.rsplit("@", 1)
            user = creds.split(":", 1)[0]
            return f"{prefix}://{user}:***@{host}"
    return DATABASE_URL


def has_permission(role: str, permission: str) -> bool:
    return permission in PERMISSIONS.get(role or "viewer", set())


def _hash_password(password: str, iterations: int = 260_000) -> str:
    if len(password) < 8:
        raise ValueError("รหัสผ่านต้องมีอย่างน้อย 8 ตัวอักษร")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return "pbkdf2_sha256${}${}${}".format(
        iterations,
        base64.urlsafe_b64encode(salt).decode("ascii"),
        base64.urlsafe_b64encode(digest).decode("ascii"),
    )


def _verify_password(password: str, encoded: str) -> bool:
    try:
        algo, iters, salt_b64, digest_b64 = encoded.split("$", 3)
        if algo != "pbkdf2_sha256":
            return False
        salt = base64.urlsafe_b64decode(salt_b64.encode("ascii"))
        expected = base64.urlsafe_b64decode(digest_b64.encode("ascii"))
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(iters))
        return secrets.compare_digest(actual, expected)
    except Exception:
        return False


def audit_event(username: str, action: str, entity_type: str = "", entity_id: str = "", detail: object = "") -> None:
    if not isinstance(detail, str):
        try:
            detail = json.dumps(detail, ensure_ascii=False, default=str)
        except Exception:
            detail = str(detail)
    with ENGINE.begin() as con:
        con.execute(insert(audit_logs).values(
            occurred_at=now_iso(), username=username or "system", action=action,
            entity_type=entity_type, entity_id=str(entity_id or ""), detail=str(detail or ""),
        ))


def init_db(seed_if_empty: bool = True) -> None:
    metadata.create_all(ENGINE)
    with ENGINE.begin() as con:
        for key, value in DEFAULT_SETTINGS.items():
            if con.execute(select(settings_table.c.key).where(settings_table.c.key == key)).first() is None:
                con.execute(insert(settings_table).values(key=key, value=value))

        user_count = con.execute(select(users.c.id)).first()
        if user_count is None:
            username = os.getenv("BOOTSTRAP_ADMIN_USERNAME", "admin").strip() or "admin"
            password = os.getenv("BOOTSTRAP_ADMIN_PASSWORD", "")
            freecloud = os.getenv("FREECLOUD_MODE", "0").strip().lower() in {"1", "true", "yes", "on"}
            if not password:
                if freecloud:
                    raise RuntimeError(
                        "ยังไม่ได้ตั้ง BOOTSTRAP_ADMIN_PASSWORD ใน Streamlit Secrets "
                        "กรุณาตั้งรหัสผ่าน Admin ก่อนเปิดระบบครั้งแรก"
                    )
                password = "Admin@12345"
            display_name = os.getenv("BOOTSTRAP_ADMIN_NAME", "ผู้ดูแลระบบ")
            con.execute(insert(users).values(
                username=username,
                password_hash=_hash_password(password),
                display_name=display_name,
                role="admin",
                active=True,
                created_at=now_iso(),
                last_login="",
                must_change_password=True,
            ))

        record_count = con.execute(select(records.c.id)).first()
        seed_pref = con.execute(select(settings_table.c.value).where(settings_table.c.key == "seed_sample")).first()
        allow_seed = bool(seed_pref and str(seed_pref[0]) == "1")
        if seed_if_empty and record_count is None and allow_seed:
            for row in SAMPLE_ROWS:
                rec = dict(zip(COLS, row))
                rh = record_fingerprint(rec)
                vals = dict(rec)
                vals.update(
                    source_hash="sample-seed", record_hash=rh, imported_at=now_iso(),
                    confidence="ตัวอย่าง", import_method="sample", created_by="system",
                )
                exists_record = con.execute(select(records.c.id).where(records.c.record_hash == rh)).first()
                if not exists_record:
                    con.execute(insert(records).values(**vals))


def get_settings() -> dict[str, str]:
    with ENGINE.connect() as con:
        rows = con.execute(select(settings_table.c.key, settings_table.c.value)).all()
    out = DEFAULT_SETTINGS.copy()
    out.update({str(k): str(v) for k, v in rows})
    return out


def set_settings(values: dict[str, object], username: str = "system") -> None:
    with ENGINE.begin() as con:
        for key, value in values.items():
            exists = con.execute(select(settings_table.c.key).where(settings_table.c.key == str(key))).first()
            if exists:
                con.execute(update(settings_table).where(settings_table.c.key == str(key)).values(value=str(value)))
            else:
                con.execute(insert(settings_table).values(key=str(key), value=str(value)))
    audit_event(username, "settings.update", "settings", "", values)


def df_all() -> pd.DataFrame:
    with ENGINE.connect() as con:
        return pd.read_sql(select(records).order_by(records.c.date_iso.desc(), records.c.period.asc(), records.c.id.desc()), con)


def imported_files_df() -> pd.DataFrame:
    with ENGINE.connect() as con:
        return pd.read_sql(select(imported_files).order_by(imported_files.c.imported_at.desc()), con)


def audit_df(limit: int = 1000) -> pd.DataFrame:
    with ENGINE.connect() as con:
        stmt = select(audit_logs).order_by(audit_logs.c.id.desc()).limit(int(limit))
        return pd.read_sql(stmt, con)


def is_file_imported(file_hash: str):
    with ENGINE.connect() as con:
        row = con.execute(select(
            imported_files.c.file_name, imported_files.c.imported_at,
            imported_files.c.imported_rows, imported_files.c.import_method,
            imported_files.c.storage_key,
        ).where(imported_files.c.file_hash == file_hash)).first()
    return tuple(row) if row else None


def insert_records(
    recs: list[dict], source_hash: str, source_name: str, import_method: str = "manual",
    warning_text: str = "", storage_key: str = "", username: str = "system",
) -> tuple[int, int]:
    now = now_iso()
    added = skipped = 0
    with ENGINE.begin() as con:
        for r in recs:
            if not r.get("date_iso") or not r.get("absent_teacher") or not r.get("substitute_teacher"):
                skipped += 1
                continue
            try:
                period = int(float(r.get("period") or 0))
            except Exception:
                period = 0
            if period <= 0:
                skipped += 1
                continue
            r = dict(r)
            r["period"] = period
            rh = record_fingerprint(r)
            vals = {k: r.get(k, "") for k in COLS}
            vals.update(
                source_hash=source_hash,
                record_hash=rh,
                imported_at=now,
                confidence=str(r.get("confidence", "")),
                import_method=str(r.get("import_method") or import_method),
                created_by=username or "system",
            )
            exists_record = con.execute(select(records.c.id).where(records.c.record_hash == rh)).first()
            if exists_record:
                skipped += 1
            else:
                try:
                    with con.begin_nested():
                        con.execute(insert(records).values(**vals))
                    added += 1
                except IntegrityError:
                    skipped += 1

        exists = con.execute(select(imported_files.c.file_hash).where(imported_files.c.file_hash == source_hash)).first()
        file_vals = dict(
            file_name=source_name, imported_at=now, imported_rows=added,
            import_method=import_method, warning_text=warning_text,
            storage_key=storage_key, uploaded_by=username or "system",
        )
        if exists:
            con.execute(update(imported_files).where(imported_files.c.file_hash == source_hash).values(**file_vals))
        else:
            con.execute(insert(imported_files).values(file_hash=source_hash, **file_vals))
    audit_event(username, "records.import", "file", source_hash, {"file": source_name, "added": added, "skipped": skipped, "method": import_method})
    return added, skipped


def delete_records(ids: Iterable[int], username: str = "system") -> int:
    ids = [int(x) for x in ids]
    if not ids:
        return 0
    with ENGINE.begin() as con:
        result = con.execute(delete(records).where(records.c.id.in_(ids)))
        n = int(result.rowcount or 0)
    audit_event(username, "records.delete", "record", ",".join(map(str, ids)), {"count": n})
    return n


def clear_sample_records(username: str = "system") -> int:
    with ENGINE.begin() as con:
        result = con.execute(delete(records).where(records.c.source_hash == "sample-seed"))
        n = int(result.rowcount or 0)
        con.execute(update(settings_table).where(settings_table.c.key == "seed_sample").values(value="0"))
    audit_event(username, "sample.clear", "record", "sample-seed", {"count": n})
    return n


def create_user(username: str, password: str, display_name: str, role: str = "viewer", created_by: str = "system", must_change_password: bool = True) -> int:
    username = username.strip().lower()
    if not username:
        raise ValueError("กรุณาระบุชื่อผู้ใช้")
    if role not in ROLE_LABELS:
        raise ValueError("สิทธิ์ผู้ใช้ไม่ถูกต้อง")
    vals = dict(
        username=username, password_hash=_hash_password(password), display_name=display_name.strip(),
        role=role, active=True, created_at=now_iso(), last_login="",
        must_change_password=bool(must_change_password),
    )
    try:
        with ENGINE.begin() as con:
            result = con.execute(insert(users).values(**vals))
            user_id = int(result.inserted_primary_key[0])
    except IntegrityError as exc:
        raise ValueError("ชื่อผู้ใช้นี้มีอยู่แล้ว") from exc
    audit_event(created_by, "user.create", "user", username, {"role": role, "display_name": display_name})
    return user_id


def authenticate_user(username: str, password: str) -> Optional[dict]:
    username = (username or "").strip().lower()
    with ENGINE.begin() as con:
        row = con.execute(select(users).where(users.c.username == username)).mappings().first()
        if not row or not bool(row["active"]) or not _verify_password(password or "", row["password_hash"]):
            return None
        con.execute(update(users).where(users.c.id == row["id"]).values(last_login=now_iso()))
        out = dict(row)
        out.pop("password_hash", None)
    audit_event(username, "auth.login", "user", username, "")
    return out


def list_users_df() -> pd.DataFrame:
    with ENGINE.connect() as con:
        stmt = select(
            users.c.id, users.c.username, users.c.display_name, users.c.role,
            users.c.active, users.c.created_at, users.c.last_login, users.c.must_change_password,
        ).order_by(users.c.username.asc())
        return pd.read_sql(stmt, con)


def update_user(user_id: int, display_name: str, role: str, active: bool, changed_by: str) -> None:
    if role not in ROLE_LABELS:
        raise ValueError("สิทธิ์ผู้ใช้ไม่ถูกต้อง")
    with ENGINE.begin() as con:
        row = con.execute(select(users.c.username).where(users.c.id == int(user_id))).first()
        if not row:
            raise ValueError("ไม่พบผู้ใช้")
        con.execute(update(users).where(users.c.id == int(user_id)).values(
            display_name=display_name.strip(), role=role, active=bool(active),
        ))
    audit_event(changed_by, "user.update", "user", str(user_id), {"role": role, "active": bool(active), "display_name": display_name})


def reset_user_password(user_id: int, new_password: str, changed_by: str, must_change: bool = True) -> None:
    with ENGINE.begin() as con:
        row = con.execute(select(users.c.username).where(users.c.id == int(user_id))).first()
        if not row:
            raise ValueError("ไม่พบผู้ใช้")
        con.execute(update(users).where(users.c.id == int(user_id)).values(
            password_hash=_hash_password(new_password), must_change_password=bool(must_change),
        ))
    audit_event(changed_by, "user.password_reset", "user", str(user_id), "")


def change_own_password(username: str, old_password: str, new_password: str) -> None:
    username = username.strip().lower()
    with ENGINE.begin() as con:
        row = con.execute(select(users).where(users.c.username == username)).mappings().first()
        if not row or not _verify_password(old_password, row["password_hash"]):
            raise ValueError("รหัสผ่านเดิมไม่ถูกต้อง")
        con.execute(update(users).where(users.c.id == row["id"]).values(
            password_hash=_hash_password(new_password), must_change_password=False,
        ))
    audit_event(username, "auth.password_change", "user", username, "")


def make_portable_backup_bytes() -> bytes:
    rec_df = df_all()
    file_df = imported_files_df()
    user_df = list_users_df()
    payload = {
        "version": 4,
        "created_at": now_iso(),
        "records": rec_df.where(pd.notna(rec_df), None).to_dict("records"),
        "imported_files": file_df.where(pd.notna(file_df), None).to_dict("records"),
        "settings": get_settings(),
        "users_public": user_df.where(pd.notna(user_df), None).to_dict("records"),
    }
    raw = json.dumps(payload, ensure_ascii=False, default=str, indent=2).encode("utf-8")
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("backup.json", raw)
        z.writestr("README.txt", "V4 portable backup: records, imported_files, settings, public user metadata. Password hashes are intentionally excluded.\n")
    return out.getvalue()


def restore_portable_backup_bytes(data: bytes, username: str) -> dict:
    with zipfile.ZipFile(io.BytesIO(data), "r") as z:
        payload = json.loads(z.read("backup.json").decode("utf-8"))
    if int(payload.get("version", 0)) != 4:
        raise ValueError("ไฟล์สำรองไม่ใช่ V4 ที่รองรับ")
    rec_rows = payload.get("records", [])
    file_rows = payload.get("imported_files", [])
    settings = payload.get("settings", {})
    with ENGINE.begin() as con:
        con.execute(delete(records))
        con.execute(delete(imported_files))
        for r in rec_rows:
            vals = {c.name: r.get(c.name) for c in records.columns if c.name != "id"}
            if not vals.get("record_hash"):
                vals["record_hash"] = record_fingerprint(vals)
            if con.execute(select(records.c.id).where(records.c.record_hash == vals["record_hash"])).first() is None:
                con.execute(insert(records).values(**vals))
        for f in file_rows:
            vals = {c.name: f.get(c.name) for c in imported_files.columns}
            fh = vals.get("file_hash")
            if fh and con.execute(select(imported_files.c.file_hash).where(imported_files.c.file_hash == fh)).first() is None:
                con.execute(insert(imported_files).values(**vals))
        for k, v in settings.items():
            exists = con.execute(select(settings_table.c.key).where(settings_table.c.key == str(k))).first()
            if exists:
                con.execute(update(settings_table).where(settings_table.c.key == str(k)).values(value=str(v)))
            else:
                con.execute(insert(settings_table).values(key=str(k), value=str(v)))
    audit_event(username, "backup.restore", "database", "portable", {"records": len(rec_rows), "files": len(file_rows)})
    return {"records": len(rec_rows), "files": len(file_rows)}


def read_v3_sqlite_bytes(data: bytes) -> dict:
    if not data.startswith(b"SQLite format 3\x00"):
        raise ValueError("ไฟล์นี้ไม่ใช่ฐานข้อมูล SQLite V3")
    with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as tmp:
        tmp.write(data)
        tmp_path = tmp.name
    try:
        con = sqlite3.connect(tmp_path)
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if "records" not in tables:
            raise ValueError("ไม่พบตาราง records ของ V3")
        recs = pd.read_sql_query("SELECT * FROM records", con).to_dict("records")
        files = pd.read_sql_query("SELECT * FROM imported_files", con).to_dict("records") if "imported_files" in tables else []
        settings = dict(con.execute("SELECT key,value FROM settings").fetchall()) if "settings" in tables else {}
        con.close()
        return {"records": recs, "imported_files": files, "settings": settings}
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


def migrate_v3_payload(payload: dict, username: str) -> dict:
    added = skipped = 0
    for r in payload.get("records", []):
        r = dict(r)
        source_hash = str(r.get("source_hash") or "v3-migration")
        source_name = str(r.get("source_file") or "V3 database")
        a, s = insert_records([r], source_hash, source_name, "v3-migration", username=username)
        added += a
        skipped += s
    if payload.get("settings"):
        set_settings(payload["settings"], username=username)
    audit_event(username, "migration.v3", "database", "v3", {"added": added, "skipped": skipped})
    return {"added": added, "skipped": skipped}


def thai_academic_year(d: date, start_mmdd: str = "05-01") -> int:
    try:
        sm, sd = [int(x) for x in start_mmdd.split("-")]
    except Exception:
        sm, sd = 5, 1
    start = date(d.year, sm, sd)
    return d.year + 543 if d >= start else d.year + 542


def academic_year_bounds(academic_year_th: int, settings: dict[str, str]) -> tuple[date, date]:
    gy = int(academic_year_th) - 543
    sm, sd = [int(x) for x in settings.get("academic_year_start", "05-01").split("-")]
    em, ed = [int(x) for x in settings.get("term2_end", "04-30").split("-")]
    start = date(gy, sm, sd)
    end_year = gy + 1 if (em, ed) < (sm, sd) else gy
    return start, date(end_year, em, ed)


def term_bounds(academic_year_th: int, term: int, settings: dict[str, str]) -> tuple[date, date]:
    gy = int(academic_year_th) - 543
    if term == 1:
        sm, sd = [int(x) for x in settings.get("academic_year_start", "05-01").split("-")]
        em, ed = [int(x) for x in settings.get("term1_end", "10-31").split("-")]
        start = date(gy, sm, sd)
        end_year = gy + 1 if (em, ed) < (sm, sd) else gy
        return start, date(end_year, em, ed)
    sm, sd = [int(x) for x in settings.get("term2_start", "11-01").split("-")]
    em, ed = [int(x) for x in settings.get("term2_end", "04-30").split("-")]
    start = date(gy, sm, sd)
    end_year = gy + 1 if (em, ed) < (sm, sd) else gy
    return start, date(end_year, em, ed)
