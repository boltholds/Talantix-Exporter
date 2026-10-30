# Talantix Exporter — Excel/CV archive v1

Read-only Talantix GraphQL exporter intended for later import into Persona AI. It creates a ZIP file containing **`Talantix_Export.xlsx` at archive root**, folders `CV/<candidate_id>/`, a manifest, and an optional `metadata/` with original JSON data.

**Current limitations:** account-specific API integration has not yet been verified. Resume data is currently saved as **text/JSON**, where available; this exporter does **not** download original PDF/DOCX files or message attachments. Email messages and the documented AVITO_CHAT channel are queried. Other external chat channels are not guaranteed. Unavailable data is marked in `Export_Info` and `manifest.json`.

## Installation

Python 3.11+. Excel files are generated using Python's standard library; the only third-party dependency is `httpx`.

```bash
python -m pip install -r requirements.txt
```

In PowerShell:

```powershell
$env:TALANTIX_TOKEN = 'YOUR_TOKEN'
$env:TALANTIX_USER_AGENT = 'Persona AI (contact@example.com)'
python export.py export --output Talantix_Export.zip
```

In Bash:

```bash
export TALANTIX_TOKEN='YOUR_TOKEN'
export TALANTIX_USER_AGENT='Persona AI (contact@example.com)'
python export.py export --output Talantix_Export.zip
```

For API schema discovery without reading candidate data, run:

```bash
python export.py --schema-only
```

`--workdir` optionally selects a staging directory. By default, each run creates a new dated subdirectory inside `.talantix_work/` to avoid mixing exports. Provided workdirs must be empty. These files contain personal data. Do not commit tokens, XLSX, CVs, backups or local staging. Store exports encrypted, limit access, and delete staging after verification.

## Archive specification

```text
Talantix_Export.zip
├── Talantix_Export.xlsx
├── CV/
│   └── <candidate_id>/
│       ├── resume_<resume_id>.json
│       └── resume_<resume_id>.txt   (only if text exists)
├── Messages/                       (only for oversized email bodies)
├── metadata/
│   ├── graphql_schema.json          (if API permits introspection)
│   ├── persons.json
│   ├── vacancies.json
│   ├── candidate_details.json
│   ├── messages.json
│   ├── history.json
│   └── errors.json
└── manifest.json
```

Excel workbook sheets: `Candidates`, `Contacts`, `CV`, `Vacancies`, `Applications`, `Messages`, `History`, `Export_Info`. Candidate and vacancy IDs are stable source keys and must be kept when loading into the Persona AI database. Dates are converted from Talantix epoch milliseconds to ISO 8601 UTC. Missing original application dates remain blank (an application update timestamp is **not** an application date).

Long email messages exceeding Excel cell limits are saved to `Messages/<candidate_id>/...`. The workbook includes their relative paths. All content is treated as data, not Excel formulas.

## Completeness

Read both `Export_Info` and `manifest.json`: `PARTIAL`, `UNSUPPORTED` and `NOT_EXPORTED` mean the ZIP is **not a full Talantix backup**. Candidate or vacancy listing failures abort the export. Candidate-detail and optional-section failures are captured in `metadata/errors.json`.

Still to implement and verify against your API permissions: download original CV files, communication attachments, all external chat types, full event payloads, reliable original application timestamps, exact full coverage of historic/archived data, and a separate Persona AI database importer.

## Documentation

- [Talantix API](https://api.talantix.ru/docs/general/)
- [Candidates](https://api.talantix.ru/docs/guides/examples/persons/)
- [Vacancies](https://api.talantix.ru/docs/guides/examples/vacancies/)
- [Communications](https://api.talantix.ru/docs/guides/examples/person_response_with_communications/)

## Fix for GraphQL complexity errors (October 2026)

A production-sized Talantix archive showed **12,445 candidates and 26 vacancies**, but the previous combined candidate detail query failed for every candidate: `Requested operation exceeds the permitted complexity limit: 2600 > 2499`. The exporter now uses separate GraphQL queries for profile, resume and response data; resume queries automatically fall back to a minimal metadata selection on a complexity-limit error. Email and history requests run independently even when another section fails.

Validate the account's schema and permissions **on only 10 candidates first**. You can reuse a previous export archive so that the 12,445 base records are not downloaded again:

```powershell
python export.py export --source-archive .\Talantix_Export.zip --limit 10 --output Talantix_Smoke_10.zip
```

Inspect `Export_Info` and `metadata/errors.json` in the new ZIP. If the first 10 are good, omit `--limit` and choose a new output filename for a full run:

```powershell
python export.py export --source-archive .\Talantix_Export.zip --output Talantix_Export_Full.zip
```

`--source-archive` reads only the previous ZIP's `metadata/persons.json` and `metadata/vacancies.json`. It still needs a valid API token to fetch the new details. The old archive remains unchanged.

**Local auth-patch users:** Your local `zip_export.py` may have changes for `TALANTIX_TOKEN_URL` that have not been committed to GitHub. Check `git status` before pulling updates, and keep a backup of the working authentication implementation. The stock GitHub CLI currently uses `TALANTIX_TOKEN`.

A successful local smoke test does not prove all candidate details or original CV binaries can be exported. In particular, `StructuredResume.downloadLink` is documented by Talantix but downloading original files is not implemented in this version.

## Recover missing chat messages without exporting CVs again

The first Excel/CV snapshot can contain zero messages even if candidates have responses and chat conversations. Talantix `person.messages` is **email-only**; the separate `person.communications.item(type: AVITO_CHAT)` API exposes Avito chat messages with sender/time/text and cursor pagination.

`recover_messages.py` makes a new ZIP from an existing export, keeping its original CV files and other raw records. The default retrieves AVITO_CHAT only, which is the channel confirmed by published Talantix GraphQL examples. **hh.ru chat history is not guaranteed available via this method**; an `HhResponse` event is evidence of an application, not evidence of messages. For other possible enum names, the tool can probe one candidate via `--channels`; unsupported channels are recorded instead of silently becoming empty conversations.

To test 20 candidates without re-downloading the original CVs (PowerShell):

```powershell
python recover_messages.py --source .\Talantix_CV.zip --limit 20 --output .\Talantix_Messages_Test.zip
```

For all candidates:

```powershell
python recover_messages.py --source .\Talantix_CV.zip --limit 0 --output .\Talantix_With_Messages.zip
```

`TALANTIX_TOKEN` or the locally installed `auth.py` integration with `TALANTIX_TOKEN_URL` is required. Existing source ZIP remains unchanged. Inspect `metadata/chat_recovery_errors.json` and the `chat_recovery` section of `manifest.json`. Even if the recovered archive contains zero messages, check whether the candidate actually has a chat in the **tested channel** in the Talantix UI.

To try another known channel identifier without changing code:

```powershell
python recover_messages.py --source .\Talantix_CV.zip --limit 20 --channels AVITO_CHAT HH_CHAT --output .\Talantix_Chat_Probe.zip
```

`HH_CHAT` is an **unverified probe**, not a documented supported enum. If the API rejects it, the diagnostic file reports the rejection. This does not prove whether hh.ru chat export is otherwise supported.

The normal `export.py export` command now also queries `AVITO_CHAT` where the API permits it. Existing CV files are not downloaded again by `recover_messages.py`.

## Restore API credentials when token URL returns HTML

If `auth_status.py --verify` reports `HTTP 200 but HTML instead of JSON` and `Matching token cache: False`, the link was accepted as a URL but did not deliver the expected token JSON. Official Talantix documentation says the link expires after 30 days or after its first token refresh. A redirect, web page, or middleware can also yield HTML. Repeatedly altering the URL does not recover a consumed token pair.

If you **already saved the original JSON token response** and its refresh token is still valid, set `TALANTIX_TOKEN_URL` to the **exact original** token link and import the JSON safely from the Windows clipboard:

```powershell
Get-Clipboard -Raw | python auth_import.py --stdin
python auth_status.py --verify
```

No tokens appear in command arguments or the terminal output. `auth_import.py` refuses to overwrite an existing cache. The CLI expects an `auth.py` that implements `LinkToken`, `_sanitize_pair` and `_persist_tokens` from the separate AutoAuth patch.

If you do **not** have a valid original JSON token pair, create a new API token in Talantix and copy the new link. Use `python auth_status.py --verify` to establish the rotating local cache immediately, before starting the full export. Keep the URL and JSON out of logs, GitHub and chat.

### Candidate-specific NOT_FOUND (October 2026)

Talantix may return `PersonCommunicationError(errorType: NOT_FOUND)` when a requested candidate/channel chat is absent. The exporter must **not** disable the entire channel based on the first candidate's `NOT_FOUND`. This behavior was fixed: every candidate in the requested sample is checked independently, `NOT_FOUND` is counted separately from real API errors, and the resulting archive contains `metadata/chat_recovery_report.json` with `chat_found`, `not_found`, `messages_received`, and `errors` by channel. We still cannot conclude that hh.ru messages are exported via the documented `AVITO_CHAT` example.

To update only the affected files while preserving local AutoAuth patches:

```powershell
$commit = "287c270ba877918fd849e6948c26ca76da1de999"
$base = "https://raw.githubusercontent.com/boltholds/Talantix-Exporter/$commit"
Invoke-WebRequest "$base/communications.py" -OutFile ".\\communications.py"
Invoke-WebRequest "$base/recover_messages.py" -OutFile ".\\recover_messages.py"
python recover_messages.py --source .\\Talantix_CV.zip --limit 20 --output .\\Talantix_Messages_Test_v2.zip
```

The source ZIP is left untouched. Inspect `metadata/chat_recovery_report.json` before processing a larger sample. If all 20 candidates yield `NOT_FOUND`, try a sample known to have an Avito chat in Talantix; random candidates from hh.ru may have no Avito conversation.

## Diagnosing hh.ru conversations

In a real candidate ZIP examined in October 2026, **12,419 candidates originated from hh.ru responses and 26 from hh.ru invitations**. Consequently, 20 consecutive `AVITO_CHAT` lookups returned `NOT_FOUND` and no messages. This is normal for records without Avito chats; do not make 12,445 Avito API requests unnecessarily.

Talantix's web UI supports hh.ru chats, but the presence of a chat in the UI does **not** automatically prove its entire history is exposed in the GraphQL API. To identify supported API channels, use the introspection diagnostic. It prints channel enum names and aggregate counts, not access tokens or candidate contact details:

```powershell
Invoke-WebRequest "https://raw.githubusercontent.com/boltholds/Talantix-Exporter/main/inspect_channels.py" -OutFile ".\inspect_channels.py"
python inspect_channels.py --source .\Talantix_CV.zip
```

Requires `TALANTIX_TOKEN`, or a working `TALANTIX_TOKEN_URL` with the optional local auth patch. The command is read-only. Do not try guessed channel names: confirm `PersonCommunicationChannel` values in the returned schema first. If no hh.ru chat channel is exposed, the exporter must record that limitation rather than silently treating the messages as empty.

Talantix's public UI documentation explains that the hh.ru chat is synchronized with hh.ru and that multiple separate chats can exist for the same candidate in different vacancy contexts.
