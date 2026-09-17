r"""
db/seed.py — Database seed / maintenance script
================================================
Run this script once to:
  1. Migrate an existing auth.db to the current schema (drops plaintext column,
     adds role column).
  2. Re-hash and re-insert seed users with proper PBKDF2 hashes.

Usage:
    # From the repo root or anywhere — the script self-resolves paths:
    python application/backend/db/seed.py

    # Or from application/ (run as a module):
    cd application
    python -m backend.db.seed

    # Or run the file directly from its own folder:
    cd application/backend/db
    python seed.py

Note:
    Do NOT run ``python -m .\seed.py`` — ``-m`` expects a dotted module name,
    not a file path. Use ``python seed.py`` (file) or ``python -m backend.db.seed``
    (module) instead.

Safe to re-run: INSERT OR REPLACE overwrites existing rows.
"""

import sys
from pathlib import Path

# Ensure 'application/' is on sys.path so `backend.*` imports resolve,
# regardless of the current working directory or how the script is launched.
_app_dir = str(Path(__file__).resolve().parents[2])
if _app_dir not in sys.path:
    sys.path.insert(0, _app_dir)

from backend.auth.local_auth import add_user, verify_user, list_users, migrate_db, init_db

# ---------------------------------------------------------------------------
# Step 1: ensure DB exists, then migrate schema
# ---------------------------------------------------------------------------
print("Initializing database...")
init_db()
print("Migrating database schema...")
migrate_db()

# ---------------------------------------------------------------------------
# Step 2: seed users
# Replace these credentials before deploying — passwords are hashed on insert
# and never stored in plaintext.
# ---------------------------------------------------------------------------
# seed_users = [
#     # (username,   password,       role)
#     ("9032746", "Albert@9032", "coach"),
#     ("9014401", "Maria@9014", "coach"),
#     ("9007102", "Paula@9007", "coach"),
#     ("9055348", "Ronaldo@9055", "manager"),
# ]
seed_users = [
    # (username,   password,       role)
    ("9055348", "Ronaldo@9055", "manager"),
    ("manager", "manager123", "manager"),
    # --- Telesales ---
    ("9007734", "Simon@9007", "manager"),    # Simon Anthony Chan
    ("9000150", "Gerald@9000", "manager"),   # Gerald Jimenez
    ("9027750", "Ram@9027", "manager"),      # Ram Fazal
    ("9014401", "Maria@9014", "coach"),      # Maria Crispina Tuason
    ("9007102", "Paula@9007", "coach"),      # Paula Joy Fabila
    # --- BGCO (STR) ---
    ("9046297", "Jenisus@9046", "manager"),  # Jenisus Mercado
    ("9050895", "Mark@9050", "coach"),       # Mark Lester Perez
    ("9040400", "Elvin@9040", "coach"),      # Elvin Balawat
    ("9040352", "Kyle@9040", "coach"),       # Kyle Rainier Inalgan
    ("9049922", "Lovely@9049", "coach"),     # Lovely Ed Lorine Reyta
    # --- BGCO (QC) ---
    ("9006565", "Joseph@9006", "manager"),   # Joseph Manalastas
    ("9025447", "Michael@9025", "coach"),    # Michael Martin Corpuz
    ("9039359", "Garry@9039", "coach"),      # Garry Jasper Hugo
    ("9047615", "Lawrence@9047", "coach"),   # Lawrence Lloyd Buenaventura
    ("9028691", "Rochee@9028", "coach"),     # Rochee Joy Magpantay
    # --- BGCO (SMF) ---
    ("9041301", "Allie@9041", "manager"),    # Allie Jane De Ocampo
    ("9022739", "Eloisa@9022", "coach"),     # Eloisa Cruz
    ("9033640", "Queenny@9033", "coach"),    # Queenny Mae Gerono
    ("9042107", "Sherwin@9042", "coach"),    # Sherwin Ilao
    ("9023442", "Roland@9023", "coach"),     # Roland Monteras
    # --- BGCO (MTY) ---
    ("3001676", "Ana@3001", "manager"),      # Ana Montemayor
    ("3000510", "Ismael@3000", "coach"),     # Ismael Rodriguez
    ("3000720", "Jessica@3000", "coach"),    # Jessica Jimenez
    ("3000658", "Andres@3000", "coach"),     # Andres Ramirez
    ("3000508", "Monica@3000", "coach"),     # Monica Fuentes
    # --- VZ Mobile Services ---
    ("9043143", "Galan@9043", "coach"),      # Galan JR, Marcelo Ruben S
]

for username, password, role in seed_users:
    add_user(username, password, role=role)
    ok = verify_user(username, password)
    print(f"  {'[OK]' if ok else '[FAIL]'} {username} ({role})")

print("\nAll users:")
for u in list_users():
    print(f"  - {u}")
