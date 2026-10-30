"""Append Talantix chat messages to an existing Excel/CV ZIP export.

Original ZIP remains unchanged. Existing CV files are streamed from old archive.
"""
from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import os
import shutil
import tempfile
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

import httpx

from archive import records, write_workbook
from communications import DEFAULT_CHANNELS, fetch_chat, chat_query
from zip_export import Client


def make_client():
    url = os.environ.get("TALANTIX_TOKEN_URL", "").strip()
    token = os.environ.get("TALANTIX_TOKEN", "").strip()
    agent = os.environ.get("TALANTIX_USER_AGENT", "Persona AI Talantix Exporter")
    uses_auth = "tokens" in inspect.signature(Client.__init__).parameters
    if uses_auth:
        from auth import LinkToken, StaticToken
        if url:
            return Client(LinkToken(url, user_agent=agent), agent)
        if token:
            return Client(StaticToken(token), agent)
        raise ValueError("Set TALANTIX_TOKEN_URL or TALANTIX_TOKEN")
    if url:
        raise ValueError("Token-link patch is not installed locally; apply the auth patch")
    if not token:
        raise ValueError("Set TALANTIX_TOKEN")
    return Client(token, agent)


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
        supported: list[str] = []
        errors: list[dict] = []
        new_count = 0
        checked = 0

        client = make_client()
        try:
            if selected:
                first_id = int(selected[0]["id"])
                for channel in channels:
                    try:
                        await fetch_chat(client, first_id, channel)
                        supported.append(channel)
                    except (ValueError, httpx.HTTPError) as exc:
                        errors.append({"phase":"probe", "channel":channel,
                                       "candidate_id":str(first_id),
                                       "error":str(exc)[:500]})
                        print("Channel unsupported or inaccessible:", channel)
            for person in selected:
                pid = str(person["id"])
                new_msgs: list[dict] = []
                for channel in supported:
                    try:
                        new_msgs.extend(await fetch_chat(client, int(pid), channel))
                    except (ValueError, httpx.HTTPError) as exc:
                        errors.append({"phase":"export","channel":channel,
                                       "candidate_id":pid,"error":str(exc)[:500]})
                old = messages.get(pid, [])
                combined = merge_messages(old, new_msgs)
                new_count += len(combined) - len(old)
                messages[pid] = combined
                checked += 1
                if checked % 100 == 0 or checked == len(selected):
                    print(f"Checked {checked}/{len(selected)} candidates; new messages: {new_count}")
        finally:
            await client.close()

        manifest = dict(manifest)
        coverage = dict(manifest.get("coverage") or {})
        coverage["chat_recovery"] = {
            "status": ("EXPORTED" if checked == len(persons) and
                       len(supported) == len(channels) and not errors else "PARTIAL"),
            "count": new_count,
            "details": (f"Checked {checked}/{len(persons)} candidates, "
                        f"available channels: {', '.join(supported) or 'none'}, "
                        f"failures: {len(errors)}. Other chat providers not guaranteed."),
        }
        coverage["external_chats"] = {
            "status": "PARTIAL" if supported else "NOT_EXPORTED",
            "count": new_count,
            "details": "Only channels queried successfully are covered.",
        }
        manifest["coverage"] = coverage
        manifest["counts"] = dict(manifest.get("counts") or {})
        manifest["counts"]["Messages"] = sum(len(msg) for msg in messages.values())
        manifest["chat_recovery"] = {
            "checked_candidates":checked,"total_candidates":len(persons),
            "supported_channels":supported,"errors":len(errors),
        }

        with tempfile.TemporaryDirectory(prefix="talantix-messages-") as tmp:
            stage = Path(tmp)
            rows = records(stage, persons, vacancies, details, histories, messages,
                           coverage, existing_cv_names=existing_names)
            workbook = write_workbook(stage, rows)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            temp_output = args.output.with_suffix(args.output.suffix + ".tmp")
            replaced = {"Talantix_Export.xlsx", "manifest.json",
                        "metadata/messages.json", "metadata/chat_recovery_errors.json"}
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
          f"supported channels: {', '.join(supported) or 'none'}")


if __name__ == "__main__":
    asyncio.run(run())
