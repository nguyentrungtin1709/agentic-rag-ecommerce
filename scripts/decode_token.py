"""Decode a Saleor JWT to inspect its claims (no signature verification).

Usage::

    uv run python scripts/decode_token.py

Then paste the token when prompted.  Press Ctrl-D / Ctrl-Z to exit.
"""

import sys

import jwt


def main() -> int:
    print("Paste Saleor JWT and press Enter (Ctrl-D / Ctrl-Z to exit):")
    token = input("> ").strip()
    if not token:
        print("No token provided.", file=sys.stderr)
        return 1

    try:
        payload = jwt.decode(token, options={"verify_signature": False})
    except Exception as exc:
        print(f"Decode failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    print("\n=== CLAIMS ===")
    for key, value in payload.items():
        print(f"  {key}: {value}")

    print()
    print("iss claim:        ", repr(payload.get("iss")), "<-- must match SALEOR_URL")
    print("Required claims:  exp, iat, sub, iss")
    for required in ("exp", "iat", "sub", "iss"):
        if required not in payload:
            print(f"  [MISSING] {required}")

    print()
    print("SALEOR_URL in .env:  http://host.docker.internal:8000")
    print("SALEOR_URL seen by app container (same as .env)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
