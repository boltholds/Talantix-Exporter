"""Inspect Talantix communication channels and aggregate archive metadata.

No candidate names, contact details, token values, or individual messages are
printed. Read-only GraphQL introspection, no mutations.

Usage:
  python inspect_channels.py --source Talantix_CV.zip
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
import json
import re
from pathlib import Path
from zipfile import ZipFile

import httpx

from recover_messages import make_client

GRAPHQL_ENDPOINTS = (
    "https://api.talantix.ru/graphql/schema",
    "https://api.talantix.ru/graphql",
)
# Introspection is requested as separate, low-complexity queries.
# One rejected __type lookup must not hide the status of other lookups.
PROBES = {
    "channel": '''query ChannelType {
        __type(name: "PersonCommunicationChannel") {
            name kind enumValues { name }
        }
    }''',
    "communication": '''query CommunicationType {
        __type(name: "PersonCommunication") {
            name kind possibleTypes { name }
        }
    }''',
}
TYPE_NAMES = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _diagnostic(response) -> str:
    """Return a bounded API error class, never raw user/request values."""
    status = response.status_code
    try:
        body = response.json()
    except (ValueError, TypeError):
        return f"HTTP {status} (non-JSON response)"
    if not isinstance(body, dict):
        return f"HTTP {status} (unexpected body)"
    if body.get("type"):
        code = str(body["type"])
        detail = str(body.get("detail") or "")
        if re.fullmatch(r"[a-zA-Z0-9_ .-]{1,100}", detail):
            return f"HTTP {status} {code}: {detail}"
        return f"HTTP {status} {code}"
    errors = body.get("errors") or []
    if errors:
        first = errors[0]
        if not isinstance(first, dict):
            return f"HTTP {status} GraphQL errors"
        msg = str(first.get("message") or "")
        ext = first.get("extensions") or {}
        code = str(ext.get("errorType") or "") if isinstance(ext, dict) else ""
        # GraphQL introspection errors can mention a rejected field or type.
        # Do not print arbitrary messages or request/authorization headers.
        if "complexity limit" in msg.lower():
            return f"HTTP {status} GRAPHQL_COMPLEXITY_LIMIT"
        if "introspection" in msg.lower() and ("disabled" in msg.lower() or "not allowed" in msg.lower()):
            return f"HTTP {status} INTROSPECTION_DISABLED"
        if "Validation error" in msg or "validation" in msg.lower():
            return f"HTTP {status} GRAPHQL_VALIDATION_ERROR"
        return f"HTTP {status} GRAPHQL_ERROR" + (f" ({code})" if re.fullmatch(r"[A-Z_]+", code) else "")
    return f"HTTP {status} (no usable schema information)"


async def introspect(client) -> dict:
    token = await client._source.access_token()
    attempts = []
    for endpoint in GRAPHQL_ENDPOINTS:
        gathered = {}
        for key, query in PROBES.items():
            try:
                response = await client._http.post(
                    endpoint,
                    json={"query": query},
                    headers={"Authorization": "Bearer " + token},
                )
                if response.status_code in (401, 403):
                    return {"status": "AUTH_DENIED", "http_status": response.status_code}
                if response.status_code in (429, 500, 502, 503, 504):
                    return {"status": "API_UNAVAILABLE", "http_status": response.status_code}
                if response.status_code != 200:
                    attempts.append(f"{endpoint.rsplit('/',1)[-1]}/{key}: {_diagnostic(response)}")
                    continue
                body = response.json()
                if body.get("errors"):
                    attempts.append(f"{endpoint.rsplit('/',1)[-1]}/{key}: {_diagnostic(response)}")
                    continue
                found = (body.get("data") or {}).get("__type")
                if not isinstance(found, dict):
                    attempts.append(f"{endpoint.rsplit('/',1)[-1]}/{key}: TYPE_NOT_FOUND")
                    continue
                gathered[key] = found
            except (ValueError, httpx.HTTPError):
                attempts.append(f"{endpoint.rsplit('/',1)[-1]}/{key}: REQUEST_OR_JSON_ERROR")
        if gathered:
            channel = gathered.get("channel") or {}
            union = gathered.get("communication") or {}
            return {
                "status": "OK" if gathered.get("channel") else "PARTIAL",
                "channel_type": channel.get("name"),
                "channels": [item.get("name") for item in channel.get("enumValues") or []
                             if isinstance(item, dict) and TYPE_NAMES.fullmatch(str(item.get("name", "")))],
                "communication_types": [item.get("name") for item in union.get("possibleTypes") or []
                                        if isinstance(item, dict)],
                "diagnostics": attempts,
            }
    return {"status": "UNAVAILABLE", "diagnostics": attempts}


def archive_summary(path: Path) -> dict:
    with ZipFile(path) as z:
        persons = json.loads(z.read("metadata/persons.json"))
        histories = json.loads(z.read("metadata/history.json"))
        if not isinstance(persons, list) or not isinstance(histories, dict):
            raise ValueError("Talantix archive has unexpected metadata structure")
        event_counts = Counter()
        hh_candidates = set()
        for pid, events in histories.items():
            for event in events or []:
                if not isinstance(event, dict):
                    continue
                name = str(event.get("__typename") or "UNKNOWN")
                event_counts[name] += 1
                if name == "HhResponse":
                    hh_candidates.add(str(pid))
        source_counts = Counter()
        for person in persons:
            source = person.get("source") or {}
            category = str(source.get("name") or "(unspecified)")
            source_counts[category] += 1
        return {
            "candidates": len(persons),
            "candidates_with_hh_response_events": len(hh_candidates),
            "event_types": event_counts.most_common(12),
            "source_categories": source_counts.most_common(12),
        }


async def main():
    parser = argparse.ArgumentParser(description="Discover channels in the Talantix schema")
    parser.add_argument("--source", type=Path, help="Optional existing Talantix ZIP")
    parser.add_argument("--no-api", action="store_true", help="Archive metadata only")
    args = parser.parse_args()
    if args.source:
        info = archive_summary(args.source)
        print("Candidates:", info["candidates"])
        print("Candidates with HhResponse history:", info["candidates_with_hh_response_events"])
        print("History event types:")
        for label, count in info["event_types"]:
            print(f"  {label}: {count}")
        print("Candidate source categories:")
        for label, count in info["source_categories"]:
            print(f"  {label}: {count}")
    if args.no_api:
        return
    client = make_client()
    try:
        data = await introspect(client)
    finally:
        await client.close()
    print("Talantix communication schema:", data["status"])
    for diagnostic in data.get("diagnostics", []):
        print("  Schema probe:", diagnostic)
    if data["status"] not in ("OK", "PARTIAL"):
        if data.get("http_status"):
            print("  HTTP:", data["http_status"])
        print("No channel list was confirmed by API introspection.")
        return
    if not data["channels"]:
        print("PersonCommunicationChannel enum values are not visible.")
    else:
        print("Supported communication channel enum values:", ", ".join(data["channels"]))
    if data["communication_types"]:
        print("PersonCommunication union variants:", ", ".join(data["communication_types"]))


if __name__ == "__main__":
    asyncio.run(main())
