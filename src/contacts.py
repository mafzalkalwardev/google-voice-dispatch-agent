import pandas as pd
from pathlib import Path

VALID_PHONE_COLUMNS = ["Phone", "phone", "PHONE", "Phone Number", "Mobile", "Number"]
VALID_NAME_COLUMNS = [
    "Name",
    "name",
    "Client",
    "legal name",
    "Legal Name",
    "Company",
    "Company Name",
    "Business Name",
]


def _find_column(columns, candidates):
    wanted = {c.strip().lower() for c in candidates}
    return next((c for c in columns if c.strip().lower() in wanted), None)


def normalize_phone(phone: str) -> str:
    phone = str(phone or "").strip()
    digits = "".join(ch for ch in phone if ch.isdigit())
    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    return digits


def load_contacts(path: str):
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Contacts file not found: {path}")

    if path.suffix.lower() in [".xlsx", ".xls"]:
        df = pd.read_excel(path)
    else:
        df = pd.read_csv(path)

    df.columns = df.columns.str.strip()
    phone_col = _find_column(df.columns, VALID_PHONE_COLUMNS)
    if phone_col is None:
        raise ValueError(f"No phone column found. Available: {list(df.columns)}")
    name_col = _find_column(df.columns, VALID_NAME_COLUMNS)

    df = df[df[phone_col].notna()]
    df[phone_col] = df[phone_col].astype(str)
    contacts = []
    for _, row in df.iterrows():
        number = normalize_phone(row[phone_col])
        if not number:
            continue
        name = row.get(name_col) if name_col else "Unknown"
        if pd.isna(name):
            name = "Unknown"
        contacts.append({"phone": number, "name": str(name).strip() or "Unknown"})

    return contacts
