import pandas as pd
from pathlib import Path
_contacts_cache: dict[str, tuple[float, int, list[dict]]] = {}

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


def load_contacts_cached(path: str | Path, *, force_reload: bool = False) -> list[dict]:
    """Load contacts with in-process cache keyed by file mtime/size."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Contacts file not found: {p}")
    stat = p.stat()
    cache_key = f"{p.resolve()}|{stat.st_size}|{int(stat.st_mtime)}"
    cached = _contacts_cache.get(cache_key)
    if not force_reload and cached:
        _mtime, _size, rows = cached
        if _mtime == stat.st_mtime and _size == stat.st_size:
            return rows
    rows = load_contacts(p)
    _contacts_cache.clear()
    _contacts_cache[cache_key] = (stat.st_mtime, stat.st_size, rows)
    return rows


def paginate_contacts(
    rows: list[dict],
    *,
    page: int = 1,
    per_page: int = 50,
    query: str = "",
) -> dict:
    """Filter by search query and return one page of results."""
    q = (query or "").strip().lower()
    if q:
        filtered = [
            c
            for c in rows
            if q in (c.get("name") or "").lower() or q in (c.get("phone") or "").lower()
        ]
    else:
        filtered = rows
    per_page = max(10, min(200, int(per_page)))
    page = max(1, int(page))
    total = len(filtered)
    pages = max(1, (total + per_page - 1) // per_page)
    if page > pages:
        page = pages
    start = (page - 1) * per_page
    slice_rows = filtered[start : start + per_page]
    return {
        "rows": slice_rows,
        "page": page,
        "per_page": per_page,
        "total": total,
        "pages": pages,
        "start": start + 1 if total else 0,
        "end": min(start + per_page, total),
    }
