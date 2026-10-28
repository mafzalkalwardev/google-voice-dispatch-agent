"""Local Google Voice account registry with Windows-encrypted credentials."""
from __future__ import annotations

import base64
import ctypes
import json
import re
import uuid
from ctypes import wintypes
from pathlib import Path
from typing import Any

from src.paths import runtime_base


ACCOUNTS_FILE = runtime_base() / "data" / "gv_accounts.json"
SESSION_MARKER = ".gv_session_ok"


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _blob(data: bytes) -> tuple[_Blob, Any]:
    buf = ctypes.create_string_buffer(data)
    return _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte))), buf


def _protect(value: str) -> str:
    if not value:
        return ""
    if not hasattr(ctypes, "windll"):
        raise RuntimeError("Credential encryption is only supported on Windows")
    source, keepalive = _blob(value.encode("utf-8"))
    output = _Blob()
    if not ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(source), "Google Voice Agent", None, None, None, 0,
        ctypes.byref(output),
    ):
        raise ctypes.WinError()
    try:
        raw = ctypes.string_at(output.pbData, output.cbData)
        return base64.b64encode(raw).decode("ascii")
    finally:
        ctypes.windll.kernel32.LocalFree(output.pbData)


def _unprotect(value: str) -> str:
    if not value:
        return ""
    source, keepalive = _blob(base64.b64decode(value))
    output = _Blob()
    if not ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(source), None, None, None, None, 0, ctypes.byref(output)
    ):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(output.pbData, output.cbData).decode("utf-8")
    finally:
        ctypes.windll.kernel32.LocalFree(output.pbData)


def make_profile_name(name: str, email: str) -> str:
    stem = name or email.split("@", 1)[0] or "google_voice"
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", stem).strip("_")
    return (stem or "google_voice")[:60]


def _normalize(row: dict[str, Any]) -> dict[str, Any]:
    email = str(row.get("email") or "").strip()
    return {
        "id": str(row.get("id") or uuid.uuid4().hex),
        "name": str(row.get("name") or email or "Google Voice").strip(),
        "email": email,
        "profile_name": str(row.get("profile_name") or make_profile_name("", email)).strip(),
        "loopback_device": str(row.get("loopback_device") or "").strip(),
        "capture_device": str(row.get("capture_device") or "").strip(),
        "enabled": bool(row.get("enabled", True)),
        "password_enc": str(row.get("password_enc") or ""),
    }


def load_accounts(*, include_password: bool = False) -> list[dict[str, Any]]:
    if not ACCOUNTS_FILE.exists():
        return []
    try:
        raw = json.loads(ACCOUNTS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    rows = [_normalize(row) for row in raw if isinstance(row, dict)]
    for row in rows:
        row["has_password"] = bool(row["password_enc"])
        if include_password:
            try:
                row["password"] = _unprotect(row["password_enc"])
            except Exception:
                row["password"] = ""
        row.pop("password_enc", None)
    return rows


def save_account(payload: dict[str, Any]) -> dict[str, Any]:
    existing: list[dict[str, Any]] = []
    if ACCOUNTS_FILE.exists():
        try:
            existing = [
                _normalize(row)
                for row in json.loads(ACCOUNTS_FILE.read_text(encoding="utf-8"))
                if isinstance(row, dict)
            ]
        except (OSError, json.JSONDecodeError):
            existing = []
    account_id = str(payload.get("id") or uuid.uuid4().hex)
    current = next((row for row in existing if row["id"] == account_id), None)
    merged = dict(current or {})
    merged.update({k: v for k, v in payload.items() if k != "password"})
    merged["id"] = account_id
    if payload.get("password"):
        merged["password_enc"] = _protect(str(payload["password"]))
    account = _normalize(merged)
    if not account["email"]:
        raise ValueError("Google account email is required")
    if not account["profile_name"]:
        raise ValueError("Chrome profile name is required")
    duplicate = next(
        (row for row in existing if row["id"] != account_id and row["profile_name"].casefold() == account["profile_name"].casefold()),
        None,
    )
    if duplicate:
        raise ValueError("Each account must use a distinct Chrome profile")
    updated = [account if row["id"] == account_id else row for row in existing]
    if current is None:
        updated.append(account)
    ACCOUNTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    ACCOUNTS_FILE.write_text(json.dumps(updated, indent=2), encoding="utf-8")
    return next(row for row in load_accounts() if row["id"] == account_id)


def delete_account(account_id: str) -> bool:
    if not ACCOUNTS_FILE.exists():
        return False
    raw = json.loads(ACCOUNTS_FILE.read_text(encoding="utf-8"))
    kept = [row for row in raw if str(row.get("id")) != account_id]
    if len(kept) == len(raw):
        return False
    ACCOUNTS_FILE.write_text(json.dumps(kept, indent=2), encoding="utf-8")
    return True


def session_marker_path(profile_name: str) -> Path:
    return runtime_base() / "chrome_profiles" / profile_name / SESSION_MARKER


def mark_session_ready(profile_name: str) -> None:
    marker = session_marker_path(profile_name)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("Google Voice session verified\n", encoding="utf-8")


def public_accounts() -> list[dict[str, Any]]:
    rows = load_accounts()
    for row in rows:
        row["session_ready"] = session_marker_path(row["profile_name"]).exists()
        row["audio_ready"] = bool(row["loopback_device"] and row["capture_device"])
    return rows

