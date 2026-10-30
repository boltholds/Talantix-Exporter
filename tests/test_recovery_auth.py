"""Chat recovery authentication regression tests (no network or credentials)."""
import os
import sys
import types
import unittest
from unittest.mock import patch

import httpx

from recover_messages import RecoveryClient, make_client


class FakeTokens:
    def __init__(self):
        self.rotations = 0
        self.closed = False

    async def access_token(self):
        return "old"

    async def refresh_after_401(self, rejected):
        self.rotations += 1
        assert rejected == "old"
        return "new"

    async def close(self):
        self.closed = True


class RecoveryAuthTests(unittest.IsolatedAsyncioTestCase):
    async def test_auto_refresh_401(self):
        tokens = FakeTokens()
        received = []

        def handler(request):
            received.append(request.headers.get("authorization"))
            if len(received) == 1:
                return httpx.Response(401)
            return httpx.Response(200, json={"data": {"person": {"id": 123}}})

        client = RecoveryClient(tokens, "unit-tests")
        await client._http.aclose()
        client._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            result = await client.query("query{ person { id }}")
            self.assertEqual(result["person"]["id"], 123)
            self.assertEqual(received, ["Bearer old", "Bearer new"])
            self.assertEqual(tokens.rotations, 1)
        finally:
            await client.close()
        self.assertTrue(tokens.closed)

    async def test_link_auth_is_independent_of_exporter_client(self):
        fake_auth = types.ModuleType("auth")
        calls = []

        def fake_link(url, user_agent):
            calls.append((url, user_agent))
            return FakeTokens()

        fake_auth.LinkToken = fake_link
        with patch.dict(sys.modules, {"auth": fake_auth}):
            with patch.dict(os.environ, {
                "TALANTIX_TOKEN_URL": "https://api.talantix.ru/docs/pages/token/?hash=test",
                "TALANTIX_TOKEN": "",
            }):
                client = make_client()
                try:
                    self.assertEqual(len(calls), 1)
                    self.assertIsInstance(client, RecoveryClient)
                finally:
                    await client.close()


if __name__ == "__main__":
    unittest.main()
