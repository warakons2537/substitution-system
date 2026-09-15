from __future__ import annotations

import argparse
import os
from pathlib import Path

from database import (
    authenticate_user, clear_sample_records, df_all, get_settings, init_db, insert_records,
)
from parser_engine import libreoffice_status, parse_document, tesseract_status
from report_engine import make_substitution_docx

p = argparse.ArgumentParser(description="V4 self test")
p.add_argument("--document", help="optional .doc/.docx/.pdf to test parser")
args = p.parse_args()

init_db()
print("DB records:", len(df_all()))
print("Settings school:", get_settings().get("school_name"))
print("Tesseract:", tesseract_status().get("message"))
print("LibreOffice:", libreoffice_status())

if args.document:
    recs, warnings, _ = parse_document(args.document, enable_ocr=True)
    print("Document records:", len(recs))
    print("Warnings:", warnings)
    if recs:
        import pandas as pd
        from datetime import date
        d = date.fromisoformat(recs[0]["date_iso"])
        teacher = recs[0]["absent_teacher"]
        day = pd.DataFrame([r for r in recs if r["date_iso"] == d.isoformat() and r["absent_teacher"] == teacher])
        docx = make_substitution_docx(day, d, teacher, get_settings())
        print("Generated DOCX bytes:", len(docx))
