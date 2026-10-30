"""Regression: NOT_FOUND on one candidate must not suppress other chats."""
import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from communications import ChatNotFound
import recover_messages


class MockClient:
    async def close(self):
        pass


class RecoverMessagesTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_first_chat_does_not_disable_channel(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "old.zip"
            output = Path(tmp) / "new.zip"
            with ZipFile(source, "w") as archive:
                fixtures = {
                    "manifest.json": {
                        "format": "talantix-export-archive",
                        "coverage": {},
                        "counts": {},
                    },
                    "metadata/persons.json": [
                        {"id": 1, "firstName": "A"},
                        {"id": 2, "firstName": "B"},
                    ],
                    "metadata/vacancies.json": [],
                    "metadata/candidate_details.json": {},
                    "metadata/history.json": {},
                    "metadata/messages.json": {},
                }
                for path, value in fixtures.items():
                    archive.writestr(path, json.dumps(value))

            seen = []

            async def fake_fetch_chat(client, person_id, channel):
                seen.append(person_id)
                if person_id == 1:
                    raise ChatNotFound("No chat")
                return [{"id": "message-1", "channel": channel, "text": "test"}]

            with patch.object(recover_messages, "make_client", return_value=MockClient()), \
                 patch.object(recover_messages, "fetch_chat", side_effect=fake_fetch_chat), \
                 patch.object(sys, "argv", [
                     "recover_messages.py", "--source", str(source),
                     "--limit", "2", "--output", str(output),
                 ]):
                await recover_messages.run()

            self.assertEqual(seen, [1, 2])
            with ZipFile(output) as archive:
                report = json.loads(archive.read("metadata/chat_recovery_report.json"))
                msgs = json.loads(archive.read("metadata/messages.json"))
                self.assertEqual(report["AVITO_CHAT"]["not_found"], 1)
                self.assertEqual(report["AVITO_CHAT"]["chat_found"], 1)
                self.assertEqual(report["AVITO_CHAT"]["messages_received"], 1)
                self.assertEqual(len(msgs["2"]), 1)
                self.assertIsNone(archive.testzip())


if __name__ == "__main__":
    unittest.main()
