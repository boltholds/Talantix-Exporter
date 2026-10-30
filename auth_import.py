"""Import an existing Talantix token JSON response into the local rotating cache.

Windows PowerShell:
  Get-Clipboard -Raw | python auth_import.py --stdin

The environment variable TALANTIX_TOKEN_URL must hold the *same exact link*
that originally produced this JSON. No token or URL is printed or sent anywhere.
"""
import argparse
import asyncio
import json
import os
import sys
import time

from auth import LinkToken, TokenError, _persist_tokens, _sanitize_pair


async def run() -> int:
    parser = argparse.ArgumentParser(description="Securely restore an existing Talantix token response")
    parser.add_argument("--stdin", action="store_true", help="Read token JSON from standard input")
    args = parser.parse_args()
    if not args.stdin:
        parser.error("Pass --stdin and pipe in the token JSON. Never put tokens on the command line.")
    link = os.environ.get("TALANTIX_TOKEN_URL", "").strip()
    if not link:
        print("TALANTIX_TOKEN_URL is not set", file=sys.stderr)
        return 2
    provider = LinkToken(link)
    try:
        if provider.cache_file.exists():
            print("Token cache already exists; refusing to overwrite it.")
            return 2
        # Input is never printed, stored as raw input, or transmitted.
        try:
            raw = sys.stdin.read(1_000_001)
            if len(raw) > 1_000_000:
                raise ValueError("too large")
            payload = json.loads(raw)
            pair = _sanitize_pair(payload, refresh_time=time.time())
        except (ValueError, TypeError, TokenError):
            print("Input is not a valid Talantix token JSON response.", file=sys.stderr)
            return 2
        if float(pair["refresh_expires_at"]) <= time.time():
            print("Existing refresh token has expired; create a new token in Talantix.")
            return 2
        try:
            _persist_tokens(provider.cache_file, pair)
        except OSError:
            print("Could not persist token cache securely.", file=sys.stderr)
            return 2
        print("Token pair stored locally. No secrets were printed.")
        print("Verify with: python auth_status.py --verify")
        return 0
    finally:
        await provider.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
