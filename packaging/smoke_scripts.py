"""CI check: libraries that in-app Python scripts rely on import in the frozen build."""

import importlib

MODULES = [
    "pandas", "duckdb", "pyarrow", "requests", "gspread", "gspread_dataframe", "openpyxl", "tqdm",
    "google.oauth2.credentials", "google.oauth2.service_account", "google.auth.transport.requests",
    "google_auth_oauthlib.flow", "concurrent.futures", "smtplib", "email.mime.text", "zipfile",
    "xml.etree.ElementTree", "sqlite3", "csv", "statistics", "zoneinfo", "pysheets.scripting",
]
missing = []
for name in MODULES:
    try:
        importlib.import_module(name)
    except Exception as e:  # noqa: BLE001
        missing.append(f"{name}: {e!r}")
if missing:
    print("MISSING\n" + "\n".join(missing))
    raise SystemExit(1)
print(f"imports ok ({len(MODULES)} modules)")
