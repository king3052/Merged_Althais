"""
cms_edits.py: CMS's official claim edits, loaded from CMS's own quarterly files, checked in code (not by the AI).

  NCCI procedure-to-procedure (PTP) edits, practitioner: pairs of codes that aren't billed together on one day, and whether
      a modifier can let them through (indicator 1) or never (0).
  MUEs, practitioner: the most units of a code one patient gets on one day, and how strictly (MAI 1 per line, 2 absolute by
      policy, 3 per day on clinical grounds).

Getting the files (each quarter, about 15 minutes):
  cms.gov > Medicare > Coding & Billing > National Correct Coding Initiative Edits >
      "Medicare NCCI Procedure to Procedure (PTP) Edits" > Practitioner PTP Edits: the four zip files of the current quarter
      "Medicare NCCI Medically Unlikely Edits (MUEs)" > Practitioner Services MUE Table: the zip
  CMS asks you to accept its license (it carries the AMA's CPT terms) before downloading. Then /admin/cms > upload.

Rows are kept in the app's own database (Cloud SQL), so every copy of the app sees them and they survive deploys. Until a
table is loaded, ncci_checker.py falls back to its short hand-picked list and MUEs aren't checked.
"""
from __future__ import annotations

import datetime as dt
import io
import json
import re
import threading
import time
import zipfile
from typing import Dict, Iterable, Iterator, List, Optional, Tuple

from sqlalchemy import Column, Integer, String, delete, func, insert, select, tuple_

from auth import Base, OrgSettings, SessionLocal, engine

META_ORG, META_CAT = "__cms__", "cms_tables"
KEEP_DELETED_DAYS = 800          # deleted edits still apply to older dates of service; older than this, nothing bills anyway
BATCH = 5000


class CmsPtp(Base):
    __tablename__ = "cms_ncci_ptp"
    col1 = Column(String(5), primary_key=True)
    col2 = Column(String(5), primary_key=True)
    eff = Column(Integer, primary_key=True)          # yyyymmdd; a pair can be deleted and later added back
    deleted = Column(Integer, nullable=True)         # yyyymmdd the edit stopped applying, or None
    mod_ind = Column(String(1), nullable=False)      # 0 never, 1 with an NCCI-associated modifier, 9 not applicable


class CmsMue(Base):
    __tablename__ = "cms_mue"
    code = Column(String(5), primary_key=True)
    max_units = Column(Integer, nullable=False)
    mai = Column(String(1), nullable=False)
    rationale = Column(String(120), default="")


Base.metadata.create_all(engine, tables=[CmsPtp.__table__, CmsMue.__table__])

MAI = {"1": "per claim line", "2": "per day, by CMS policy (no exceptions)", "3": "per day, on clinical grounds"}

# ── reading CMS's files ────────────────────────────────────────────────────────────────────────────────────────────────
_PTP = re.compile(r"^\s*([A-Z0-9]{5})\s+([A-Z0-9]{5})\s+(?:\*\s+)?(\d{8})\s+(\*|\d{8})\s+([019])\b")


def _cell(v) -> str:
    if v is None:
        return ""
    if isinstance(v, (dt.datetime, dt.date)):
        return v.strftime("%Y%m%d")
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v).strip()


def _lines(name: str, src) -> Iterator[str]:
    """Each row of one CMS file as text with fields separated by 2+ spaces, whatever the format (.txt tab-separated, .csv,
    .xlsx). src: bytes or a binary file object (read a line at a time, so a 60 MB file never sits in memory twice)."""
    low = name.lower()
    if low.endswith(".xlsx"):
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(src if isinstance(src, bytes) else src.read()), read_only=True, data_only=True)
        for ws in wb.worksheets:
            for row in ws.iter_rows(values_only=True):
                yield "  ".join(_cell(v) for v in row)
        return
    stream = io.BytesIO(src) if isinstance(src, bytes) else src
    for raw in io.TextIOWrapper(stream, encoding="utf-8", errors="ignore", newline=""):
        line = raw.rstrip("\r\n")
        if low.endswith(".csv"):
            line = re.sub(r'"', "", re.sub(r',(?=(?:[^"]*"[^"]*")*[^"]*$)', "\t", line))
        yield line.replace("\t", "  ")


def _files(name: str, data: bytes) -> Iterator[Tuple[str, object]]:
    """The data files inside an upload (a zip, possibly holding zips) or the upload itself, as (name, bytes or open file).
    Prefers .txt/.csv over .xlsx when a zip has both, so each row is read once."""
    if name.lower().endswith(".zip") or data[:2] == b"PK" and not name.lower().endswith(".xlsx"):
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names = [n for n in z.namelist() if not n.endswith("/") and "__macosx" not in n.lower()]
            for n in names:
                if n.lower().endswith(".zip"):
                    yield from _files(n, z.read(n))
            text = [n for n in names if n.lower().endswith((".txt", ".csv"))]
            use = text or [n for n in names if n.lower().endswith(".xlsx")]
            for n in use:
                with z.open(n) as f:
                    yield n, f
    else:
        yield name, data


def parse_ptp(uploads: Iterable[Tuple[str, bytes]], today: Optional[dt.date] = None) -> Iterator[dict]:
    cutoff = int(((today or dt.date.today()) - dt.timedelta(days=KEEP_DELETED_DAYS)).strftime("%Y%m%d"))
    for name, data in uploads:
        for fname, fdata in _files(name, data):
            for line in _lines(fname, fdata):
                m = _PTP.match(line.upper())
                if not m:
                    continue
                c1, c2, eff, dl, mi = m.groups()
                deleted = None if dl == "*" else int(dl)
                if deleted is not None and deleted < cutoff:
                    continue
                yield {"col1": c1, "col2": c2, "eff": int(eff), "deleted": deleted, "mod_ind": mi}


def parse_mue(uploads: Iterable[Tuple[str, bytes]]) -> Iterator[dict]:
    for name, data in uploads:
        for fname, fdata in _files(name, data):
            for line in _lines(fname, fdata):
                f = [x.strip() for x in re.split(r"\s{2,}", line.strip().upper())]
                if len(f) >= 3 and re.fullmatch(r"[A-Z0-9]{5}", f[0]) and f[1].isdigit() and f[2][:1] in "123" and f[2][:1]:
                    yield {"code": f[0], "max_units": int(f[1]), "mai": f[2][0], "rationale": (f[3] if len(f) > 3 else "")[:120].title()}


# ── loading ────────────────────────────────────────────────────────────────────────────────────────────────────────────
_status: Dict[str, dict] = {}
_lock = threading.Lock()


def meta(db) -> dict:
    row = db.scalar(select(OrgSettings).where(OrgSettings.org_key == META_ORG, OrgSettings.category == META_CAT))
    try:
        return json.loads(row.data) if row and row.data else {}
    except ValueError:
        return {}


def _save_meta(db, kind: str, info: dict):
    row = db.scalar(select(OrgSettings).where(OrgSettings.org_key == META_ORG, OrgSettings.category == META_CAT))
    if row is None:
        row = OrgSettings(org_key=META_ORG, category=META_CAT, data="{}")
        db.add(row)
    doc = json.loads(row.data or "{}")
    doc[kind] = info
    row.data = json.dumps(doc)


def _insert_skipping_repeats(table):
    """INSERT that skips a row whose key is already there (a code listed twice in CMS's files), on Postgres and SQLite."""
    name = engine.dialect.name
    if name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        return pg_insert(table).on_conflict_do_nothing()
    if name == "sqlite":
        from sqlalchemy.dialects.sqlite import insert as sl_insert
        return sl_insert(table).on_conflict_do_nothing()
    return insert(table)


def load(db, kind: str, uploads: List[Tuple[str, bytes]], today: Optional[dt.date] = None) -> dict:
    """Replace one table (kind "ptp" or "mue") with the rows in these CMS files, in one transaction: until it commits, checks
    keep using the old rows. Refuses an upload with almost no rows (the wrong file), so a bad upload can't empty a table."""
    table = CmsPtp if kind == "ptp" else CmsMue
    rows = parse_ptp(uploads, today) if kind == "ptp" else parse_mue(uploads)
    t0 = time.time()
    db.execute(delete(table))
    stmt = _insert_skipping_repeats(table)
    n, batch = 0, []
    for r in rows:
        batch.append(r)
        if len(batch) >= BATCH:
            db.execute(stmt, batch)
            n += len(batch)
            batch = []
            _status[kind] = {"state": "loading", "rows": n}
    if batch:
        db.execute(stmt, batch)
        n += len(batch)
    if n < (1000 if kind == "ptp" else 100):
        db.rollback()
        raise ValueError(f"Only {n} rows found: that doesn't look like CMS's {'Practitioner PTP' if kind == 'ptp' else 'Practitioner MUE'} file. Nothing was changed.")
    info = {"rows": n, "files": [nm for nm, _ in uploads], "loadedAt": dt.datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "seconds": round(time.time() - t0, 1)}
    _save_meta(db, kind, info)
    db.commit()
    _cache.clear()
    return info


def load_in_background(kind: str, uploads: List[Tuple[str, bytes]]) -> bool:
    """Start loading on a thread (the PTP files are a few million lines). False if a load of this table is already running."""
    with _lock:
        if (_status.get(kind) or {}).get("state") == "loading":
            return False
        _status[kind] = {"state": "loading", "rows": 0}

    def work():
        db = SessionLocal()
        try:
            info = load(db, kind, uploads)
            _status[kind] = {"state": "done", **info}
        except Exception as e:
            db.rollback()
            _status[kind] = {"state": "error", "error": str(e)[:300]}
            print(f"[CMS] {kind} load failed: {type(e).__name__}: {str(e)[:200]}", flush=True)
        finally:
            db.close()
    threading.Thread(target=work, daemon=True).start()
    return True


def status(db) -> dict:
    m = meta(db)
    return {k: {**(m.get(k) or {}), **({"job": _status[k]} if k in _status else {})} for k in ("ptp", "mue")}


# ── checking ───────────────────────────────────────────────────────────────────────────────────────────────────────────
_cache: Dict[str, Tuple[float, object]] = {}


def loaded(kind: str) -> bool:
    """Whether CMS's table of this kind is in the database (cached for 5 minutes)."""
    hit = _cache.get("loaded:" + kind)
    if hit and time.time() - hit[0] < 300:
        return bool(hit[1])
    db = SessionLocal()
    try:
        table = CmsPtp if kind == "ptp" else CmsMue
        ok = db.scalar(select(func.count()).select_from(select(table).limit(1).subquery())) > 0
    except Exception:
        ok = False
    finally:
        db.close()
    _cache["loaded:" + kind] = (time.time(), ok)
    return ok


def _dos(dos) -> int:
    if isinstance(dos, (dt.date, dt.datetime)):
        return int(dos.strftime("%Y%m%d"))
    try:
        return int(dt.date.fromisoformat(str(dos)[:10]).strftime("%Y%m%d"))
    except (TypeError, ValueError):
        return int(dt.date.today().strftime("%Y%m%d"))


def ptp_pairs(codes: List[str], dos=None) -> Dict[Tuple[str, str], str]:
    """{(column 1, column 2): modifier indicator} for every pair of these codes with an NCCI edit in force on the date of service."""
    codes = sorted({str(c).strip().upper() for c in codes if c})
    if len(codes) < 2:
        return {}
    pairs = [(a, b) for a in codes for b in codes if a != b]
    d = _dos(dos)
    db = SessionLocal()
    try:
        rows = db.execute(select(CmsPtp.col1, CmsPtp.col2, CmsPtp.mod_ind, CmsPtp.eff, CmsPtp.deleted)
                          .where(tuple_(CmsPtp.col1, CmsPtp.col2).in_(pairs))).all()
    except Exception:              # databases without row-value IN: one pair at a time (a claim has a handful)
        rows = []
        for a, b in pairs:
            rows += db.execute(select(CmsPtp.col1, CmsPtp.col2, CmsPtp.mod_ind, CmsPtp.eff, CmsPtp.deleted)
                               .where(CmsPtp.col1 == a, CmsPtp.col2 == b)).all()
    finally:
        db.close()
    out = {}
    for c1, c2, mi, eff, deleted in rows:
        if eff <= d and (deleted is None or deleted > d):
            out[(c1, c2)] = mi
    return out


def mue_limits(codes: List[str]) -> Dict[str, Tuple[int, str, str]]:
    """{code: (most units per day, MAI, rationale)} for these codes."""
    codes = sorted({str(c).strip().upper() for c in codes if c})
    if not codes:
        return {}
    db = SessionLocal()
    try:
        return {r.code: (r.max_units, r.mai, r.rationale or "") for r in db.scalars(select(CmsMue).where(CmsMue.code.in_(codes)))}
    finally:
        db.close()


def mue_problems(lines: List[dict]) -> List[str]:
    """Plain-English flags for claim lines whose units are over CMS's MUE. lines: [{"code", "units"}]. Units of the same code on
    several lines add up for MAI 2 and 3 (per day); MAI 1 is per line."""
    if not lines or not loaded("mue"):
        return []
    lim = mue_limits([l.get("code") for l in lines])
    per_day: Dict[str, int] = {}
    out = []
    for l in lines:
        code = str(l.get("code") or "").strip().upper()
        try:
            u = int(l.get("units") or 1)
        except (TypeError, ValueError):
            u = 1
        if code not in lim:
            continue
        limit, mai, why = lim[code]
        if mai == "1" and u > limit:
            out.append(f"{code}: {u} units on one line is over CMS's limit of {limit} {MAI[mai]} (MUE). Split it only if the note documents separate sites or sessions; otherwise bill {limit}.")
        per_day[code] = per_day.get(code, 0) + u
    for code, u in per_day.items():
        limit, mai, why = lim[code]
        if mai in ("2", "3") and u > limit:
            out.append(f"{code}: {u} units is over CMS's limit of {limit} {MAI[mai]} (MUE{', ' + why if why else ''}). "
                       + ("Units above it are denied, no appeal." if mai == "2" else f"Bill {limit}, or appeal with records if the note truly supports more."))
    return out
