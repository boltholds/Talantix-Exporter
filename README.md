# Talantix Exporter — Excel/CV archive v1

Read-only Talantix GraphQL exporter intended for later import into Persona AI. It creates a ZIP file containing **`Talantix_Export.xlsx` at archive root**, folders `CV/<candidate_id>/`, a manifest, and an optional `metadata/` with original JSON data.

**Current limitations:** account-specific API integration has not yet been verified. Resume data is currently saved as **text/JSON**, where available; this exporter does **not** download original PDF/DOCX files or message attachments. Only email history is queried, not every external chat channel. Unavailable data is marked in `Export_Info` and `manifest.json`.

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
