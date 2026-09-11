"""
NCCI PTP (Procedure-to-Procedure) edit checker for Althais.

HOW THIS WORKS:
  NCCI edits define code pairs where Column 1 (payable) and Column 2
  (bundled/denied) should not be billed together on the same claim for
  the same patient on the same date of service. If they are, the Column 2
  code is denied — causing a real claim rejection.

HOW TO GET THE FULL CMS TABLE (do this quarterly):
  1. Go to: cms.gov/medicare/coding-billing/national-correct-coding-initiative-ncci-edits
  2. Download all "Practitioner PTP Edits" ZIP files
  3. Unzip each file — you get pipe-delimited .txt files
  4. Run: python3 ncci_checker.py --load path/to/unzipped/files/
  5. Rebuilds ncci_edits.db with the full real table
  6. Repeat every quarter (Jan, Apr, Jul, Oct)
"""

import sqlite3, os, sys
from pathlib import Path

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ncci_edits.db")

CURATED_EDITS = [
    # E/M bundled with minor procedures — modifier 25 on E/M needed to separate
    ("99281","12001","0"),("99282","12001","0"),("99283","12001","0"),
    ("99284","12001","0"),("99285","12001","0"),
    ("99281","12002","0"),("99282","12002","0"),("99283","12002","0"),
    ("99284","12002","0"),("99285","12002","0"),
    ("99281","12011","0"),("99282","12011","0"),("99283","12011","0"),
    ("99284","12011","0"),("99285","12011","0"),
    # Critical care — common components bundled in
    ("99291","36415","0"),("99291","94760","0"),("99291","94761","0"),
    ("99291","93005","0"),("99291","71046","0"),
    # Injection with E/M — modifier 25 on E/M may separate
    ("96372","99213","1"),("96372","99214","1"),("96372","99215","1"),
    ("96372","99283","1"),("96372","99284","1"),("96372","99285","1"),
    # IV infusion push conflict
    ("96365","96374","0"),("96366","96374","0"),
    # ECG bundled with E/M
    ("93000","99281","0"),("93000","99282","0"),
    ("93010","99283","0"),("93010","99284","0"),("93010","99285","0"),
    # Pulse ox bundled with E/M
    ("94760","99283","0"),("94760","99284","0"),("94760","99285","0"),
    ("94761","99283","0"),("94761","99284","0"),("94761","99285","0"),
    # Catheter conflict
    ("51701","51702","0"),
    # Wound care
    ("97597","97598","0"),
    # Laceration + E/M
    ("12031","12001","0"),("12032","12001","0"),
    ("12041","12001","0"),("12042","12001","0"),
    # Splinting with fracture care
    ("29125","25600","0"),("29125","25605","0"),
    ("29126","25600","0"),("29126","25605","0"),
    # Casting with fracture care
    ("29075","25600","0"),("29085","25600","0"),
    # UA components
    ("81001","81003","0"),("81002","81003","0"),
    # Strep test
    ("87880","87081","0"),
    # Critical care subsequent
    ("99292","99291","0"),
    # Blood draw bundled with infusion
    ("36415","96360","0"),("36415","96365","0"),("36415","96374","0"),
    # Global surgery — E/M during postop
    ("99213","10060","0"),("99214","10060","0"),
    ("99213","10061","0"),("99214","10061","0"),
]

_db_initialized = False

def _ensure_db():
    global _db_initialized
    if _db_initialized:
        return
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS ptp_edits (
        col1 TEXT NOT NULL,
        col2 TEXT NOT NULL,
        modifier_indicator TEXT NOT NULL DEFAULT '0',
        PRIMARY KEY (col1, col2)
    )""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_col1 ON ptp_edits(col1)")
    existing = c.execute("SELECT COUNT(*) FROM ptp_edits").fetchone()[0]
    if existing == 0:
        for col1, col2, mi in CURATED_EDITS:
            c.execute("INSERT OR IGNORE INTO ptp_edits VALUES (?,?,?)", (col1, col2, mi))
    conn.commit()
    conn.close()
    _db_initialized = True

def load_from_cms_file(directory: str):
    """Load real CMS quarterly NCCI files into the DB."""
    txt_files = list(Path(directory).glob("*.txt"))
    if not txt_files:
        print(f"No .txt files found in {directory}")
        return
    _ensure_db()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    total = 0
    for fpath in txt_files:
        print(f"Loading {fpath.name}...")
        count = 0
        with open(fpath, encoding="utf-8", errors="ignore") as f:
            for line in f:
                parts = line.strip().split()
                if len(parts) < 3:
                    continue
                col1, col2 = parts[0].strip(), parts[1].strip()
                mi = parts[-1].strip() if len(parts) >= 5 else "0"
                if len(col1) >= 5 and len(col2) >= 5:
                    c.execute("INSERT OR REPLACE INTO ptp_edits VALUES (?,?,?)", (col1, col2, mi))
                    count += 1
        print(f"  {count:,} edits from {fpath.name}")
        total += count
    conn.commit()
    final = c.execute("SELECT COUNT(*) FROM ptp_edits").fetchone()[0]
    conn.close()
    print(f"Done. Total: {final:,} pairs from {len(txt_files)} file(s)")

def check_claim_ncci(cpt_codes: list) -> list:
    """Check CPT codes against NCCI edits. Returns list of conflict dicts."""
    _ensure_db()
    if not cpt_codes or len(cpt_codes) < 2:
        return []
    codes = list(set(str(c).strip().upper() for c in cpt_codes if c))
    conflicts = []
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        for i, code_a in enumerate(codes):
            for code_b in codes[i + 1:]:
                for col1, col2 in [(code_a, code_b), (code_b, code_a)]:
                    row = c.execute(
                        "SELECT modifier_indicator FROM ptp_edits WHERE col1=? AND col2=?",
                        (col1, col2)
                    ).fetchone()
                    if row:
                        mi = str(row["modifier_indicator"]).strip()
                        modifier_allowed = mi == "1"
                        if modifier_allowed:
                            msg = (f"Possible NCCI bundling: {col2} is typically bundled into "
                                   f"{col1} — an NCCI-associated modifier (25, 57, 59, XE, XS, "
                                   f"XU, XP) may allow separate billing if services are clinically "
                                   f"distinct. Verify before submitting.")
                        else:
                            msg = (f"NCCI bundling conflict: {col2} is bundled into {col1} and "
                                   f"cannot be billed separately — no modifier can override this. "
                                   f"Remove {col2} or verify services were genuinely separate.")
                        conflicts.append({"col1": col1, "col2": col2,
                                          "modifier_allowed": modifier_allowed, "message": msg})
                        break
        conn.close()
    except Exception as e:
        print(f"NCCI check skipped (DB error): {e}")
    return conflicts

def check_claim_ncci_flags(cpt_codes: list) -> list:
    """Returns plain-English flag strings for the validate-claim response."""
    return [c["message"] for c in check_claim_ncci(cpt_codes)]

if __name__ == "__main__":
    if "--load" in sys.argv:
        idx = sys.argv.index("--load")
        load_from_cms_file(sys.argv[idx + 1] if len(sys.argv) > idx + 1 else ".")
    else:
        print("NCCI self-test")
        _ensure_db()
        tests = [
            (["99283","12001"], True,  "E/M + laceration repair"),
            (["99214","99215"], False, "Two E/M codes — not NCCI"),
            (["99291","94760"], True,  "Critical care + pulse ox"),
            (["96372","99214"], True,  "Injection + E/M (modifier may help)"),
            (["99213","99214"], False, "Two office E/M — not NCCI"),
        ]
        all_ok = True
        for codes, expect, label in tests:
            found = len(check_claim_ncci(codes)) > 0
            ok = found == expect
            all_ok = all_ok and ok
            print(f"  {'PASS' if ok else 'FAIL'} | {label}")
        print("ALL PASS" if all_ok else "SOME FAILURES")
