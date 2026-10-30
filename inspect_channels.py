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
INTROSPECTION = """query CommunicationTypes {
  channel: __type(name:"PersonCommunicationChannel") {
    name kind enumValues { name }
  }
  communication: __type(name:"PersonCommunication") {
    name kind possibleTypes { name }
  }
  avito: __type(name:"AvitoChat") {
    name kind fields { name }
  }
}"""
TYPE_NAMES = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


async def introspect(client) -> dict:
    token = await client._source.access_token()
    failures = []
    for endpoint in GRAPHQL_ENDPOINTS:
        try:
            response = await client._http.post(
                endpoint,
                json={"query": INTROSPECTION},
                headers={"Authorization": "Bearer " + token},
            )
            if response.status_code in (401, 403):
                return {"status": "AUTH_DENIED", "http_status": response.status_code}
            if response.status_code != 200:
                failures.append(f"{endpoint.rsplit('/',1)[-1]}: HTTP {response.status_code}")
                continue
            body = response.json()
            if body.get("errors"):
                failures.append("Introspection rejected by GraphQL")
                continue
            data = body.get("data") or {}
            channel = data.get("channel") or {}
            union = data.get("communication") or {}
            avito = data.get("avito") or {}
            return {
                "status": "OK",
                "channel_type": channel.get("name"),
                "channels": [item.get("name") for item in channel.get("enumValues") or []
                             if isinstance(item, dict) and TYPE_NAMES.fullmatch(str(item.get("name","")))],
                "communication_types": [item.get("name") for item in union.get("possibleTypes") or []
                                        if isinstance(item, dict)],
                "avito_fields": [item.get("name") for item in avito.get("fields") or []
                                 if isinstance(item, dict)],
            }
        except (ValueError, httpx.HTTPError):
            failures.append(f"Could not query {endpoint.rsplit('/',1)[-1]}")
    return {"status": "UNAVAILABLE", "reason": "; ".join(failures)}


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
    if data["status"] != "OK":
        print(data.get("reason") or ("HTTP " + str(data.get("http_status", "unknown"))))
        return
    if not data["channels"]:
        print("PersonCommunicationChannel enum was not visible in the GraphQL introspection response.")
    else:
        print("Supported communication channel enum values:", ", ".join(data["channels"]))
    if data["communication_types"]:
        print("PersonCommunication union variants:", ", ".join(data["communication_types"]))


if __name__ == "__main__":
    asyncio.run(main())
