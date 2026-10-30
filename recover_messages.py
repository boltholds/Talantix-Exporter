"""Append Talantix chat messages to an existing Excel/CV ZIP export.

Original ZIP remains unchanged. Existing CV files are streamed from old archive.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import tempfile
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

import httpx

from archive import records, write_workbook
from communications import DEFAULT_CHANNELS, ChatNotFound, fetch_chat, chat_query


class _StaticToken:
    """Access-token-only fallback, requiring no other module."""
    def __init__(self, token: str):
        self._token = token

    async def access_token(self) -> str:
        return self._token

    async def refresh_after_401(self, rejected: str) -> str:
        raise ValueError("Access token rejected; configure TALANTIX_TOKEN_URL for renewal")

    async def close(self) -> None:
        pass


class RecoveryClient:
    """GraphQL client independent of zip_export.Client's constructor signature."""

    def __init__(self, source, agent: str):
        self._source = source
        self._http = httpx.AsyncClient(
            timeout=90, headers={"User-Agent": agent, "Content-Type": "application/json"},
        )

    async def query(self, query: str, variables=None) -> dict:
        bearer = await self._source.access_token()
        refreshed = False
        for attempt in range(6):
            try:
                response = await self._http.post(
                    "https://api.talantix.ru/graphql",
                    json={"query": query, "variables": variables or {}},
                    headers={"Authorization": "Bearer " + bearer},
                )
            except (httpx.TransportError, httpx.TimeoutException):
                if attempt == 5:
                    raise
                await asyncio.sleep(min(30, 2 ** attempt))
                continue
            if response.status_code == 401:
                if refreshed:
                    raise ValueError("Talantix rejected refreshed credentials")
                bearer = await self._source.refresh_after_401(bearer)
                refreshed = True
                continue
            if response.status_code in (429, 500, 502, 503, 504) and attempt < 5:
                await asyncio.sleep(min(30, 2 ** attempt))
                continue
            response.raise_for_status()
            result = response.json()
            if result.get("errors"):
                raise ValueError(str(result["errors"])[:1200])
            data = result.get("data")
            if not isinstance(data, dict):
                raise ValueError("Talantix returned an invalid GraphQL response")
            return data
        raise RuntimeError("Talantix GraphQL retries exhausted")

    async def close(self) -> None:
        try:
            await self._http.aclose()
        finally:
            await self._source.close()


def make_client():
    """Reuse existing auth.py token cache without inspecting zip_export.Client."""
    token_url = os.environ.get("TALANTIX_TOKEN_URL", "").strip()
    token = os.environ.get("TALANTIX_TOKEN", "").strip()
    agent = os.environ.get("TALANTIX_USER_AGENT", "Persona AI Talantix Exporter")
    if token_url:
        try:
            from auth import LinkToken
        except ImportError:
            raise ValueError(
                "auth.py is missing. Copy auth.py from Talantix AutoAuth Patch "
                "into the repository root."
            ) from None
        source = LinkToken(token_url, user_agent=agent)
    elif token:
        source = _StaticToken(token)
    else:
        raise ValueError("Set TALANTIX_TOKEN_URL or TALANTIX_TOKEN")
    return RecoveryClient(source, agent)


def read_json(z: ZipFile, name: str):
    return json.loads(z.read(name))


def merge_messages(old: list, new: list) -> list:
    seen: set[tuple[str, ...]] = set()
    result: list[dict] = []
    for msg in old + new:
        if not isinstance(msg, dict):
            continue
        key = (str(msg.get("channel") or "EMAIL"),
               str(msg.get("id") or ""),
               str(msg.get("createdAt") or ""),
               str(msg.get("text") or ""))
        if key not in seen:
            result.append(msg)
            seen.add(key)
    return result


async def run():
    parser = argparse.ArgumentParser(description="Restore Talantix chats to an existing ZIP")
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--limit", type=int, default=20,
                        help="First N candidates; 0 = all; default = 20")
    parser.add_argument("--channels", nargs="+", default=list(DEFAULT_CHANNELS),
                        help="GraphQL channel enums, default: AVITO_CHAT")
    args = parser.parse_args()
    if args.limit < 0:
        parser.error("--limit cannot be negative")
    if args.source.resolve() == args.output.resolve():
        parser.error("--source and --output must be different")
    channels = list(dict.fromkeys(args.channels))
    for channel in channels:
        chat_query(channel)  # Validate against GraphQL/URL injection.

    with ZipFile(args.source) as original:
        manifest = read_json(original, "manifest.json")
        if manifest.get("format") != "talantix-export-archive":
            parser.error("Not a Talantix archive")
        persons = read_json(original, "metadata/persons.json")
        vacancies = read_json(original, "metadata/vacancies.json")
        details = read_json(original, "metadata/candidate_details.json")
        histories = read_json(original, "metadata/history.json")
        messages = read_json(original, "metadata/messages.json")
        existing_names = set(original.namelist())
        selected = persons if args.limit == 0 else persons[:args.limit]
        errors: list[dict] = []
        new_count = 0
        checked = 0
        channel_stats = {
            channel: {"not_found": 0, "chat_found": 0, "messages_received": 0, "errors": 0}
            for channel in channels
        }

        client = make_client()
        try:
            # A NOT_FOUND on one candidate must never disable the channel
            # for all other candidates. Do not probe only the first candidate.
            for person in selected:
                pid = str(person["id"])
                new_msgs: list[dict] = []
                for channel in channels:
                    stats = channel_stats[channel]
                    try:
                        found = await fetch_chat(client, int(pid), channel)
                        stats["chat_found"] += 1
                        stats["messages_received"] += len(found)
                        new_msgs.extend(found)
                    except ChatNotFound:
                        # Talantix did not find this candidate's specific chat.
                        stats["not_found"] += 1
                    except (ValueError, httpx.HTTPError) as exc:
                        stats["errors"] += 1
                        errors.append({"phase": "export", "channel": channel,
                                       "candidate_id": pid, "error": str(exc)[:500]})
                old = messages.get(pid, [])
                combined = merge_messages(old, new_msgs)
                new_count += len(combined) - len(old)
                messages[pid] = combined
                checked += 1
                if checked % 100 == 0 or checked == len(selected):
                    print(f"Checked {checked}/{len(selected)} candidates; new messages: {new_count}")
        finally:
            await client.close()

        supported = [channel for channel,stats in channel_stats.items()
                     if stats["chat_found"] > 0]
        not_found_total = sum(stats["not_found"] for stats in channel_stats.values())
        error_total = sum(stats["errors"] for stats in channel_stats.values())
        print(f"Chat results: chat_found={sum(s['chat_found'] for s in channel_stats.values())}, "
              f"not_found={not_found_total}, errors={error_total}")
        for channel,stats in channel_stats.items():
            print(f"  {channel}: chats={stats['chat_found']}, "
                  f"not_found={stats['not_found']}, messages={stats['messages_received']}, "
                  f"errors={stats['errors']}")

        manifest = dict(manifest)
        coverage = dict(manifest.get("coverage") or {})
        coverage["chat_recovery"] = {
            "status": "PARTIAL" if checked < len(persons) or error_total else "EXPORTED",
            "count": new_count,
            "details": (f"Checked {checked}/{len(persons)} candidates across "
                        f"{len(channels)} requested channels; "
                        f"chats found: {sum(s['chat_found'] for s in channel_stats.values())}, "
                        f"NOT_FOUND: {not_found_total}, errors: {error_total}. "
                        "Other providers are not included unless explicitly requested."),
        }
        coverage["external_chats"] = {
            "status": "PARTIAL",
            "count": new_count,
            "details": ("Queried channels: " + ", ".join(channels) +
                        "; counts per channel: metadata/chat_recovery_report.json. "
                        "NOT_FOUND is per-candidate; it does not mean the API channel is unsupported."),
        }
        manifest["coverage"] = coverage
        manifest["counts"] = dict(manifest.get("counts") or {})
        manifest["counts"]["Messages"] = sum(len(msg) for msg in messages.values())
        manifest["chat_recovery"] = {
            "checked_candidates": checked,
            "total_candidates": len(persons),
            "requested_channels": channels,
            "channels_with_chats": supported,
            "not_found": not_found_total,
            "errors": error_total,
        }

        with tempfile.TemporaryDirectory(prefix="talantix-messages-") as tmp:
            stage = Path(tmp)
            rows = records(stage, persons, vacancies, details, histories, messages,
                           coverage, existing_cv_names=existing_names)
            workbook = write_workbook(stage, rows)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            temp_output = args.output.with_suffix(args.output.suffix + ".tmp")
            replaced = {"Talantix_Export.xlsx", "manifest.json",
                        "metadata/messages.json", "metadata/chat_recovery_errors.json",
                        "metadata/chat_recovery_report.json"}
            try:
                with ZipFile(temp_output,"w",ZIP_DEFLATED,compresslevel=6) as out:
                    for info in original.infolist():
                        if info.filename in replaced:
                            continue
                        with original.open(info) as src, out.open(info.filename,"w") as dst:
                            shutil.copyfileobj(src,dst,256*1024)
                    out.write(workbook, "Talantix_Export.xlsx")
                    out.writestr("manifest.json",json.dumps(manifest,ensure_ascii=False,indent=2))
                    out.writestr("metadata/messages.json",json.dumps(messages,ensure_ascii=False))
                    out.writestr("metadata/chat_recovery_errors.json",
                                 json.dumps(errors,ensure_ascii=False,indent=2))
                    out.writestr("metadata/chat_recovery_report.json",
                                 json.dumps(channel_stats,ensure_ascii=False,indent=2))
                    root = stage/"Messages"
                    if root.exists():
                        for f in root.rglob("*"):
                            if f.is_file() and f.relative_to(stage).as_posix() not in existing_names:
                                out.write(f, f.relative_to(stage).as_posix())
                with ZipFile(temp_output) as result:
                    if result.testzip() is not None:
                        raise ValueError("Created ZIP integrity check failed")
                temp_output.replace(args.output)
            finally:
                temp_output.unlink(missing_ok=True)
    print(f"Created: {args.output}; new messages: {new_count}; "
          f"channels with chats: {', '.join(supported) or 'none'}")


if __name__ == "__main__":
    asyncio.run(run())
