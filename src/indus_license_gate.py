"""Exit if INDUS subscription license is missing or expired."""
from __future__ import annotations

import sys
from pathlib import Path

from indus_license import verify_license


def require_license(root: str | Path | None = None) -> None:
    base = Path(root or Path(__file__).resolve().parent)
    result = verify_license(str(base))
    if not result.ok:
        print(result.message, file=sys.stderr)
        sys.exit(1)
