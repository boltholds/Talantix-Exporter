"""Read-only Talantix GraphQL backup: candidates, vacancies and schema."""
import argparse
import asyncio
import json
import os
import random
from datetime import datetime, timezone
from pathlib import Path

import httpx

URL = 'https://api.talantix.ru/graphql'
SCHEMA_URL = 'https://api.talantix.ru/graphql/schema'
PERSONS = '''query ExportPersons($after: String) {
  persons(after: $after) {
    pageInfo { hasNextPage endCursor }
    items {
      id firstName middleName lastName birthDay age
      area { name }
      source { name }
      contacts { items { type value } }
      resumes { items { __typename ... on ResumeItem { name source { type } } } }
    }
  }
}'''
VACANCIES = '''query ExportVacancies($after: String) {
  vacancies(after: $after) {
    pageInfo { hasNextPage endCursor }
    items { id title department createdAt status salary { currency from to } }
  }
}'''
INTROSPECT = '''query ExportSchema { __schema { queryType { name } types {
  kind name fields { name args { name type { kind name ofType { kind name } } }
  type { kind name ofType { kind name } } } } } }'''


def save_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(path)


class Talantix:
    def __init__(self, token: str, user_agent: str):
        self.client = httpx.AsyncClient(timeout=90, headers={
            'Authorization': f'Bearer {token}', 'Content-Type': 'application/json',
            'User-Agent': user_agent,
        })

    async def close(self):
        await self.client.aclose()

    async def query(self, query: str, variables=None, url=URL):
        for attempt in range(6):
            try:
                response = await self.client.post(url, json={'query': query, 'variables': variables or {}})
                if response.status_code in (429, 500, 502, 503, 504):
                    if attempt == 5:
                        response.raise_for_status()
                    retry = response.headers.get('Retry-After', '')
                    await asyncio.sleep(float(retry) if retry.isdigit() else min(30, 2 ** attempt + random.random()))
                    continue
                response.raise_for_status()
                body = response.json()
                if body.get('errors'):
                    raise RuntimeError(f"GraphQL errors: {json.dumps(body['errors'], ensure_ascii=False)[:1500]}")
                return body['data']
            except (httpx.TimeoutException, httpx.TransportError):
                if attempt == 5:
                    raise
                await asyncio.sleep(min(30, 2 ** attempt + random.random()))
        raise RuntimeError('Retries exhausted')


async def export_collection(api: Talantix, root: Path, name: str, query: str):
    folder = root / name
    state_path = folder / 'checkpoint.json'
    state = json.loads(state_path.read_text('utf-8')) if state_path.exists() else {
        'after': None, 'next_page': 1, 'done': False, 'items': 0,
    }
    if state['done']:
        print(f'{name}: already complete ({state["items"]} records)')
        return state
    seen_cursors = set()
    while True:
        data = await api.query(query, {'after': state['after']})
        connection = data[name]
        items = connection.get('items') or []
        info = connection.get('pageInfo') or {}
        page = state['next_page']
        save_json(folder / f'page_{page:07d}.json', connection)
        state['items'] += len(items)
        state['next_page'] += 1
        state['after'] = info.get('endCursor')
        state['done'] = not bool(info.get('hasNextPage'))
        save_json(state_path, state)
        print(f'{name}: page={page}, fetched={len(items)}, total={state["items"]}')
        if state['done']:
            return state
        if not state['after'] or state['after'] in seen_cursors:
            raise RuntimeError(f'{name}: pagination stopped, missing/repeating cursor')
        seen_cursors.add(state['after'])
        await asyncio.sleep(0.25)


async def main():
    parser = argparse.ArgumentParser(description='Read-only Talantix backup (first stage)')
    parser.add_argument('--output', default='talantix_backup')
    parser.add_argument('--schema-only', action='store_true')
    parser.add_argument('--skip-schema', action='store_true')
    args = parser.parse_args()
    token = os.environ.get('TALANTIX_TOKEN', '').strip()
    if not token:
        parser.error('Set TALANTIX_TOKEN (do not put secrets in command line arguments)')
    root = Path(args.output)
    root.mkdir(parents=True, exist_ok=True)
    api = Talantix(token, os.environ.get('TALANTIX_USER_AGENT', 'PersonaAI Talantix Exporter (internal migration)'))
    try:
        schema_ok = False
        if not args.skip_schema:
            try:
                schema = await api.query(INTROSPECT, url=SCHEMA_URL)
                save_json(root / 'graphql_schema.json', schema)
                schema_ok = True
                print('GraphQL schema saved')
            except (httpx.HTTPError, RuntimeError, ValueError) as error:
                print(f'Schema introspection unavailable: {error}')
        if args.schema_only:
            return
        results = {}
        for name, query in [('persons', PERSONS), ('vacancies', VACANCIES)]:
            results[name] = await export_collection(api, root, name, query)
        save_json(root / 'manifest.json', {
            'exported_at_utc': datetime.now(timezone.utc).isoformat(),
            'source': URL, 'schema_saved': schema_ok or (root / 'graphql_schema.json').exists(),
            'collections': results,
            'coverage': {
                'persons': 'basic profile, contacts, resume labels/source',
                'vacancies': 'basic vacancy metadata',
                'application_history': 'NOT_EXPORTED',
                'full_resumes': 'NOT_EXPORTED',
                'cover_letters': 'NOT_EXPORTED',
                'correspondence': 'NOT_EXPORTED',
                'attachments': 'NOT_EXPORTED',
            },
        })
        print(f'Backup saved to: {root.resolve()}')
    finally:
        await api.close()


if __name__ == '__main__':
    asyncio.run(main())
