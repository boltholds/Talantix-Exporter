# Talantix Exporter — stage 1

Read-only exporter using the official Talantix GraphQL endpoint. Saves raw API data in UTF-8 JSON, one page per file, with resumable cursor checkpoints. It does **not** modify Talantix.

## Setup

Python 3.11+

```bash
python -m venv .venv
source .venv/bin/activate # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Put `TALANTIX_TOKEN` into your local environment (or use a secrets manager). Do not commit the token, print it, or send it to chat.

```bash
export TALANTIX_TOKEN='your_access_token'
export TALANTIX_USER_AGENT='Your Company (technical-contact@example.com)'
python export.py --output ./private_backup
```

On PowerShell:

```powershell
$env:TALANTIX_TOKEN = 'your_access_token'
$env:TALANTIX_USER_AGENT = 'Your Company (technical-contact@example.com)'
python export.py --output ./private_backup
```

To discover schema alone:

```bash
python export.py --schema-only --output ./private_backup
```

## Scope and limitations

This first working stage exports candidate basic fields (including contacts and resume labels), vacancy basics and GraphQL introspection where permitted. It does **not yet** export complete resume bodies/files, cover letters, application links/timestamps, comments, history or correspondence. Those require schema-driven implementation and verification against account-specific API permissions. Do not treat the output as a full backup until the manifest reports all required sections complete.

Files can contain personal information. Keep the backup in an encrypted, access-controlled directory; avoid sharing raw files publicly. Incremental export is not implemented; restarting resumes unfinished cursor pages and skips completed collections.

Official references:
- https://api.talantix.ru/docs/general/
- https://api.talantix.ru/docs/guides/examples/persons/
- https://api.talantix.ru/docs/guides/examples/vacancies/
- https://api.talantix.ru/docs/guides/examples/person_response_with_communications/
