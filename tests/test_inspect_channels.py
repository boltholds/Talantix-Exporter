"""Offline tests for Talantix archive diagnostics and split introspection."""
import json
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from inspect_channels import archive_summary, introspect


class ArchiveSummaryTests(unittest.TestCase):
    def test_aggregates_hh_events_without_contact_info(self):
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


class FakeResponse:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self.body = body

    def json(self):
        return self.body


class FakeHttp:
    def __init__(self, *, deny=False):
        self.calls = []
        self.deny = deny

    async def post(self, url, **kwargs):
        self.calls.append((url, kwargs["json"]["query"]))
        if self.deny:
            if url.endswith("/schema"):
                return FakeResponse(400, {"type": "invalid_request", "detail": "invalid request"})
            return FakeResponse(200, {
                "errors": [{"message": "Validation error: introspection disabled"}], "data": None
            })
        query = kwargs["json"]["query"]
        if "ChannelType" in query:
            body = {"data": {"__type": {
                "name": "PersonCommunicationChannel",
                "enumValues": [{"name": "AVITO_CHAT"}, {"name": "HH_CHAT"}],
            }}}
        else:
            body = {"data": {"__type": {
                "name": "PersonCommunication",
                "possibleTypes": [{"name": "AvitoChat"}],
            }}}
        return FakeResponse(200, body)


class IntrospectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_fetches_channel_values_separately(self):
        client = type("FakeClient", (), {})()
        client._source = FakeTokens()
        client._http = FakeHttp()
        result = await introspect(client)
        self.assertEqual(result["status"], "OK")
        self.assertEqual(result["channels"], ["AVITO_CHAT", "HH_CHAT"])
        self.assertEqual(result["communication_types"], ["AvitoChat"])
        self.assertEqual(len(client._http.calls), 2)

    async def test_reports_prevalidation_and_graphql_errors_safely(self):
        client = type("FakeClient", (), {})()
        client._source = FakeTokens()
        client._http = FakeHttp(deny=True)
        result = await introspect(client)
        self.assertEqual(result["status"], "UNAVAILABLE")
        self.assertEqual(len(result["diagnostics"]), 4)
        self.assertTrue(any("HTTP 400 invalid_request" in x for x in result["diagnostics"]))
        self.assertTrue(any("INTROSPECTION_DISABLED" in x for x in result["diagnostics"]))
        self.assertFalse(any("fake-token" in x for x in result["diagnostics"]))


if __name__ == "__main__":
    unittest.main()
