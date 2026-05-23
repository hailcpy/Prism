"""Copy .env.example -> .env and fill required secrets on first run.

Idempotent: if .env already exists, leaves it alone. Inside the freshly copied
file, only blank values for known-secret keys are populated; anything the user
has already set is preserved.
"""

from __future__ import annotations

import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / ".env.example"
TARGET = ROOT / ".env"


def _make_fernet_key() -> str:
    try:
        from cryptography.fernet import Fernet
    except ImportError:
        # cryptography is a backend dep; in a host shell it may be absent.
        # Fall back to a 32-byte url-safe base64 string (Fernet accepts this).
        import base64

        return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
    return Fernet.generate_key().decode()


# Keys we will auto-fill if (and only if) they're blank in the freshly copied file.
SECRETS = {
    "REDIS_PASSWORD": lambda: secrets.token_urlsafe(32),
    "PRISM_CREDS_KEY": _make_fernet_key,
}


def _fill(content: str) -> str:
    out: list[str] = []
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            out.append(line)
            continue
        key, _, value = line.partition("=")
        if key in SECRETS and not value.strip():
            out.append(f"{key}={SECRETS[key]()}")
        else:
            out.append(line)
    return "\n".join(out) + ("\n" if content.endswith("\n") else "")


def main() -> int:
    if TARGET.exists():
        print(f"bootstrap_env: {TARGET.name} already exists; leaving it untouched")
        return 0
    if not EXAMPLE.exists():
        print(f"bootstrap_env: missing {EXAMPLE}", file=sys.stderr)
        return 1
    filled = _fill(EXAMPLE.read_text())
    TARGET.write_text(filled)
    print(f"bootstrap_env: created {TARGET.name} from {EXAMPLE.name} with generated secrets")
    print("bootstrap_env: add provider keys in Settings (Phase 9) after services come up")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
