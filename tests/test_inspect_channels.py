"""Offline archive/source summary and GraphQL introspection tests."""
import json
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from inspect_channels import archive_summary, introspect


class ArchiveSummaryTests(unittest.TestCase):
    def test_aggregates_hh_response_events_without_contact_info(self):
        with tempfile.TemporaryDirectory() as folder:
            archive = Path(folder) / "fixture.zip"
            with ZipFile(archive, "w") as z:
                z.writestr("metadata/persons.json", json.dumps([
                    {"id": 1, "source": {"name": "Отклик, hh.ru"}},
                    {"id": 2, "source": {"name": "Приглашение, hh.ru"}}
                ]))
                z.writestr("metadata/history.json", json.dumps({
                    "1": [{"__typename": "HhResponse"}],
                    "2": [{"__typename": "HhResponse"}, {"__typename": "Updated"}],
                }))
            stats = archive_summary(archive)
            self.assertEqual(stats["candidates"], 2)
            self.assertEqual(stats["candidates_with_hh_response_events"], 2)
            self.assertEqual(stats["event_types"][0], ("HhResponse", 2))


class FakeTokens:
    async def access_token(self):
        return "fake-token"


class FakeHttp:
    def __init__(self):
        self.urls = []
    async def post(self,url,**kwargs):
        self.urls.append(url)
        class Response:
            status_code = 200
            def json(self):
                return {"data":{
                    "channel":{"name":"PersonCommunicationChannel",
                               "enumValues":[{"name":"AVITO_CHAT"},{"name":"HH_CHAT"}]},
                    "communication":{"name":"PersonCommunication","possibleTypes":[{"name":"AvitoChat"}]},
                    "avito":{"fields":[{"name":"items"}]},
                }}
        return Response()


class IntrospectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_fetches_channel_values_without_candidate_data(self):
        client = type("FakeClient", (), {})()
        client._source = FakeTokens()
        client._http = FakeHttp()
        result = await introspect(client)
        self.assertEqual(result["status"], "OK")
        self.assertEqual(result["channels"], ["AVITO_CHAT", "HH_CHAT"])
        self.assertEqual(len(client._http.urls), 1)


if __name__ == "__main__":
    unittest.main()
