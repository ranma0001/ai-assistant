"""Accomplishment report: built ONLY from confirmed log entries with context=work in the date range.

Always a DRAFT. The raw text of every entry is kept (log_entries.raw_text) so reports can be regenerated.
The formal-English rewrite is optional (LLM); the fallback just tidies the user's own words.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import LogEntry, Report
from app.timeutil import TZ

log = logging.getLogger(__name__)


def fallback_formal(raw: str) -> str:
    t = re.sub(r"\s+", " ", raw).strip(" .,;")
    return (t[:1].upper() + t[1:] + ".") if t else t


def collect(s: Session, start: date, end: date) -> list[LogEntry]:
    return list(s.scalars(select(LogEntry).where(
        LogEntry.context == "work", LogEntry.log_date >= start, LogEntry.log_date <= end,
    ).order_by(LogEntry.log_date, LogEntry.id)))


def formalize(entries: list[LogEntry], formalizer, model: str) -> tuple[dict[int, str], str]:  # noqa: ANN001
    """Returns ({entry_id: sentence}, model_label). Falls back per-entry if the LLM omits/invalidates one."""
    out: dict[int, str] = {}
    label = "none (raw text tidied)"
    if formalizer is not None and entries:
        try:
            out = formalizer.formalize([(e.id, e.raw_text) for e in entries], model)
            label = model
        except Exception as e:  # noqa: BLE001
            log.warning("formalizer failed: %s", e)
    by_id = {e.id: e for e in entries}
    for eid, sentence in list(out.items()):
        # guard against invention: numbers in the sentence must exist in the user's own words
        if eid in by_id and set(re.findall(r"\d+", sentence)) - set(re.findall(r"\d+", by_id[eid].raw_text)):
            log.warning("formal sentence for entry %s introduced new numbers; using raw text", eid)
            del out[eid]
    for e in entries:
        out.setdefault(e.id, fallback_formal(e.raw_text))
    return out, label


def build_report(s: Session, start: date, end: date, *, fmt: str = "docx", settings=None,  # noqa: ANN001
                 formalizer=None, now: datetime | None = None) -> tuple[Path | None, int]:
    entries = collect(s, start, end)
    if not entries:
        return None, 0
    sentences, model = formalize(entries, formalizer, getattr(settings, "llm_report_model", ""))
    out_dir = Path(getattr(settings, "report_dir", "data/reports"))
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"accomplishment_{start:%Y%m%d}_{end:%Y%m%d}.{fmt}"
    rows = [(e.log_date, sentences[e.id], e.raw_text, e.ipcr_tag or "") for e in entries]
    meta = {
        "author": getattr(settings, "report_author", ""), "office": getattr(settings, "report_office", ""),
        "model": model,
    }
    (write_xlsx if fmt == "xlsx" else write_docx)(path, start, end, rows, meta)
    s.add(Report(period_start=start, period_end=end, file_path=str(path), model=model,
                 created_at=now or datetime.now(TZ)))
    return path, len(entries)


def write_docx(path: Path, start: date, end: date, rows, meta: dict) -> None:  # noqa: ANN001
    from docx import Document
    from docx.shared import Pt

    doc = Document()
    doc.add_heading("ACCOMPLISHMENT REPORT", level=1)
    if meta.get("office"):
        doc.add_paragraph(meta["office"])
    if meta.get("author"):
        doc.add_paragraph(f"Name: {meta['author']}")
    doc.add_paragraph(f"Period covered: {start:%B %d, %Y} to {end:%B %d, %Y}")
    p = doc.add_paragraph("DRAFT – for review. Statements are generated only from the officer's own logs.")
    p.runs[0].italic = True
    table = doc.add_table(rows=1, cols=4)
    table.style = "Table Grid"
    for c, h in zip(table.rows[0].cells, ["Date", "Accomplishment", "Original log entry", "IPCR / Remarks"]):
        c.text = h
        for r in c.paragraphs[0].runs:
            r.bold = True
    last = None
    for d, sentence, raw, tag in rows:
        cells = table.add_row().cells
        cells[0].text = d.strftime("%b %d, %Y") if d != last else ""
        cells[1].text = sentence
        cells[2].text = raw
        cells[3].text = tag
        last = d
    for row in table.rows:
        for c in row.cells:
            for par in c.paragraphs:
                for r in par.runs:
                    r.font.size = Pt(10)
    doc.save(path)


def write_xlsx(path: Path, start: date, end: date, rows, meta: dict) -> None:  # noqa: ANN001
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font

    wb = Workbook()
    ws = wb.active
    ws.title = "Accomplishments"
    ws.append(["ACCOMPLISHMENT REPORT (DRAFT)"])
    ws.append([f"Period: {start:%b %d, %Y} - {end:%b %d, %Y}", meta.get("author", ""), meta.get("office", "")])
    ws.append([])
    ws.append(["Date", "Accomplishment", "Original log entry", "IPCR / Remarks"])
    for c in ws[4]:
        c.font = Font(bold=True)
    ws["A1"].font = Font(bold=True, size=14)
    for d, sentence, raw, tag in rows:
        ws.append([d, sentence, raw, tag])
    for col, w in zip("ABCD", (14, 70, 50, 24)):
        ws.column_dimensions[col].width = w
    for row in ws.iter_rows(min_row=5):
        for c in row:
            c.alignment = Alignment(wrap_text=True, vertical="top")
        row[0].number_format = "mmm dd, yyyy"
    wb.save(path)
