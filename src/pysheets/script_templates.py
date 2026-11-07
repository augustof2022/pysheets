"""Starter scripts shown in the Script editor's "New" menu."""

TEMPLATES = [
    {
        "id": "consolidate",
        "name": "Consolidate Google Sheets from a directory",
        "kind": "macro",
        "code": '''"""Pull the same tab from every spreadsheet listed in a directory and stack them.

Like 01_extract_ku_ir_sources_to_parquet.py, but the result lands in a sheet.
(For a no-code version use Data > Consolidate Google Sheets, which can also Refresh.)
"""
DIRECTORY = "https://docs.google.com/spreadsheets/d/YOUR_DIRECTORY_ID/edit"
DIRECTORY_TAB = "app_cluster_directory"
TAB = "monitoring"

directory = ps.gsheet(DIRECTORY, DIRECTORY_TAB)
frames = []
for i, row in directory.iterrows():
    sid = row.get("Spreadsheet ID") or row.get("gsheet_datasource_url")
    if not sid:
        continue
    try:
        df = ps.gsheet(sid, TAB)
    except Exception as e:
        print(f"!! {row.get('cluster_id')}: {e}")
        continue
    df.columns = [" ".join(str(c).split()).upper() for c in df.columns]  # tolerant headers
    df["_cluster_id"] = row.get("cluster_id")
    frames.append(df)
    ps.progress(i + 1, len(directory), f"Pulled {row.get('cluster_id')}")
    print(f"{row.get('cluster_id')}: {len(df):,} rows")

all_rows = pd.concat(frames, ignore_index=True)
ps.new_sheet(f"{TAB}_all", all_rows)
ps.toast(f"Consolidated {len(all_rows):,} rows from {len(frames)} spreadsheets")
''',
    },
    {
        "id": "checks",
        "name": "Data quality checks (SQL)",
        "kind": "macro",
        "code": '''"""Run checks with SQL - every sheet is a table named after its tab."""
SHEET = "monitoring_all"   # change me

checks = {
    "Missing household ID": f\'\'\'SELECT * FROM "{SHEET}" WHERE coalesce(trim("HOUSEHOLD ID NUMBER"), '') = ''\'\'\',
    "Duplicate household ID": f\'\'\'SELECT * FROM "{SHEET}" QUALIFY count(*) OVER (PARTITION BY "HOUSEHOLD ID NUMBER") > 1\'\'\',
}
summary = []
for name, sql in checks.items():
    bad = ps.sql(sql)
    summary.append({"check": name, "rows": len(bad)})
    print(f"{name}: {len(bad):,}")
    if len(bad):
        ps.new_sheet(f"Check - {name}", bad)
ps.new_sheet("Check summary", pd.DataFrame(summary))
''',
    },
    {
        "id": "functions",
        "name": "Custom functions",
        "kind": "functions",
        "code": '''"""Functions defined here can be used in formulas, e.g. =HHID_OK(C2) or =CLEAN_NAME(A2).

Type hints pick the argument/return types (str, float, int, bool, date).
Save the script to (re)register the functions.
"""
import re


@ps.function
def HHID_OK(hhid: str) -> bool:
    """TRUE when the household ID is 18 digits."""
    return bool(re.fullmatch(r"\\d{18}", (hhid or "").strip()))


@ps.function
def CLEAN_NAME(name: str) -> str:
    """Trim, collapse spaces and Title-case a name."""
    return " ".join((name or "").split()).title()


@ps.function(vectorized=True)
def PERCENT_OF(part: float, whole: float) -> float:
    """Vectorised: receives whole columns as pandas Series - fast on millions of rows."""
    return (part / whole.where(whole != 0)) * 100
''',
    },
    {
        "id": "file",
        "name": "Run an existing .py file",
        "kind": "file",
        "path": "C:\\\\PythonScripts\\\\ku_ir_consolidator\\\\scripts\\\\00_run_ku_ir_pipeline.py",
        "args": "--no-open-dashboard",
        "python": "auto",
        "code": "",
    },
    {
        "id": "blank",
        "name": "Blank script",
        "kind": "macro",
        "code": """# ps.sheets()                      -> list of tab names
# ps.sheet("Sales").df()           -> pandas DataFrame
# ps.sql('SELECT * FROM "Sales"')  -> DataFrame (tabs are SQL tables)
# ps.new_sheet("Out", df)          -> write a DataFrame to a tab
# ps.gsheet(url_or_id, "tab")      -> read a Google Sheets tab
# ps.run_file("C:/path/script.py") -> run an existing script, output appears below
print(ps.sheets())
""",
    },
]
