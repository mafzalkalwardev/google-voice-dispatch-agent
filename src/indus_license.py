"""INDUS Web Agency subscription license verification."""
from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

DEFAULT_VERIFY_URL = "https://indus-web-agency.vercel.app/api/license/verify"
LICENSE_GLOB_PREFIX = "indus-license"
OFFLINE_GRACE_HOURS = 48


@dataclass
class LicenseRecord:
    product: str
    product_slug: str
    expires_at: str
    period: str
    license_token: str
    verify_url: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LicenseRecord":
        return cls(
            product=str(data.get("product") or ""),
            product_slug=str(data.get("productSlug") or data.get("product_slug") or ""),
            expires_at=str(data.get("expiresAt") or data.get("expires_at") or ""),
            period=str(data.get("period") or ""),
            license_token=str(data.get("licenseToken") or data.get("license_token") or ""),
            verify_url=str(data.get("verifyUrl") or data.get("verify_url") or DEFAULT_VERIFY_URL),
        )


@dataclass
class LicenseCheckResult:
    ok: bool
    reason: str = ""
    message: str = ""
    expires_at: str = ""
    days_remaining: int = 0
    product_slug: str = ""
    email: str = ""
    offline: bool = False


def skip_license_check() -> bool:
    return os.environ.get("INDUS_SKIP_LICENSE", "").strip().lower() in {
        "1", "true", "yes", "on",
    }


def license_search_dirs(root: str) -> list[str]:
    dirs = [root]
    data_dir = os.path.join(root, "data")
    if os.path.isdir(data_dir):
        dirs.append(data_dir)
    return dirs


def find_license_file(root: str) -> str | None:
    for folder in license_search_dirs(root):
        try:
            names = sorted(os.listdir(folder))
        except OSError:
            continue
        for name in names:
            lower = name.lower()
            if lower.startswith(LICENSE_GLOB_PREFIX) and lower.endswith(".json"):
                return os.path.join(folder, name)
    return None


def load_license(path: str) -> LicenseRecord:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    record = LicenseRecord.from_dict(data)
    if not record.license_token:
        raise ValueError("License file is missing licenseToken")
    return record


def _cache_path(root: str) -> str:
    cache_dir = os.path.join(root, "data")
    os.makedirs(cache_dir, exist_ok=True)
    return os.path.join(cache_dir, "indus_license_cache.json")


def read_verify_cache(root: str) -> dict[str, Any] | None:
    path = _cache_path(root)
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def write_verify_cache(root: str, payload: dict[str, Any]) -> None:
    path = _cache_path(root)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


def _parse_iso(value: str) -> datetime | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def local_expired(expires_at: str) -> bool:
    dt = _parse_iso(expires_at)
    if dt is None:
        return True
    return datetime.now(timezone.utc) >= dt


def days_remaining(expires_at: str) -> int:
    dt = _parse_iso(expires_at)
    if dt is None:
        return 0
    delta = dt - datetime.now(timezone.utc)
    return max(0, int(delta.total_seconds() // 86400))


def _jwt_exp(token: str) -> datetime | None:
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return None
        payload = parts[1]
        payload += "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload.encode("ascii")))
        exp = data.get("exp")
        if not exp:
            return None
        return datetime.fromtimestamp(int(exp), tz=timezone.utc)
    except (ValueError, json.JSONDecodeError, OSError):
        return None


def verify_online(record: LicenseRecord, timeout_sec: float = 12.0) -> LicenseCheckResult:
    url = (record.verify_url or DEFAULT_VERIFY_URL).strip()
    body = json.dumps({"licenseToken": record.license_token}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            data = json.loads(exc.read().decode("utf-8"))
        except (ValueError, OSError):
            data = {"valid": False, "error": f"HTTP {exc.code}"}
    except urllib.error.URLError as exc:
        return LicenseCheckResult(
            ok=False,
            reason="network",
            message=f"Could not reach license server: {exc.reason}",
        )

    if data.get("valid"):
        expires_at = str(data.get("expiresAt") or record.expires_at)
        return LicenseCheckResult(
            ok=True,
            reason="valid",
            message="License active",
            expires_at=expires_at,
            days_remaining=int(data.get("daysRemaining") or days_remaining(expires_at)),
            product_slug=str(data.get("productSlug") or record.product_slug),
            email=str(data.get("email") or ""),
        )

    reason = str(data.get("reason") or "invalid")
    return LicenseCheckResult(
        ok=False,
        reason=reason,
        message=str(data.get("error") or "License is not valid"),
        expires_at=str(data.get("expiresAt") or record.expires_at),
    )


def verify_license(root: str, path: str | None = None) -> LicenseCheckResult:
    if skip_license_check():
        return LicenseCheckResult(ok=True, reason="skipped", message="License check disabled")

    license_path = path or find_license_file(root)
    if not license_path:
        return LicenseCheckResult(
            ok=False,
            reason="missing",
            message=(
                "No INDUS license file found. Download your product from "
                "https://indus-web-agency.vercel.app/dashboard — the license "
                "file is included with the download."
            ),
        )

    try:
        record = load_license(license_path)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return LicenseCheckResult(
            ok=False,
            reason="invalid_file",
            message=f"License file could not be read: {exc}",
        )

    if local_expired(record.expires_at):
        jwt_exp = _jwt_exp(record.license_token)
        if jwt_exp and datetime.now(timezone.utc) >= jwt_exp:
            return LicenseCheckResult(
                ok=False,
                reason="expired",
                message="Your subscription has expired. Renew at indus-web-agency.vercel.app",
                expires_at=record.expires_at,
                product_slug=record.product_slug,
            )

    online = verify_online(record)
    if online.ok:
        write_verify_cache(
            root,
            {
                "verifiedAt": datetime.now(timezone.utc).isoformat(),
                "expiresAt": online.expires_at,
                "productSlug": online.product_slug,
                "licensePath": license_path,
            },
        )
        return online

    if online.reason == "network":
        cache = read_verify_cache(root)
        if cache and cache.get("licensePath") == license_path:
            verified_at = _parse_iso(str(cache.get("verifiedAt") or ""))
            if verified_at is not None:
                age_hours = (datetime.now(timezone.utc) - verified_at).total_seconds() / 3600
                expires_at = str(cache.get("expiresAt") or record.expires_at)
                if age_hours <= OFFLINE_GRACE_HOURS and not local_expired(expires_at):
                    return LicenseCheckResult(
                        ok=True,
                        reason="offline_grace",
                        message="License verified offline (server unreachable)",
                        expires_at=expires_at,
                        days_remaining=days_remaining(expires_at),
                        product_slug=record.product_slug,
                        offline=True,
                    )
        return online

    return online


def expires_at_display(expires_at: str) -> str:
    dt = _parse_iso(expires_at)
    if dt is None:
        return expires_at or "Unknown"
    return dt.astimezone().strftime("%Y-%m-%d %H:%M:%S")
