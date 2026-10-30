import unittest

from communications import chat_query, fetch_chat


class FakeApi:
    def __init__(self):
        self.calls = []

    async def query(self, query, variables):
        self.calls.append(variables)
        if len(self.calls) == 1:
            return {"person": {"id": variables["id"], "communications": {"item": {
                "__typename": "AvitoChat",
                "items": [{"__typename": "ThirdPartyChatMessageItem",
                           "id": 1, "createdAt": 1, "text": "Hello",
                           "authorType": "PERSON", "authorName": "Candidate"}],
                "pageInfo": {"hasNextPage": True, "endCursor": "cursor-1"}
            }}}}
        return {"person": {"id": variables["id"], "communications": {"item": {
            "__typename": "AvitoChat",
            "items": [{"__typename": "ManagerChatMessageItem",
                       "id": 2, "createdAt": 2, "text": "Reply",
                       "manager": {"id": 42, "firstName": "Manager", "lastName": "One"}}],
            "pageInfo": {"hasNextPage": False, "endCursor": "cursor-2"}
        }}}}


class CommunicationsTests(unittest.IsolatedAsyncioTestCase):
    async def test_chat_pagination_and_authors(self):
        api = FakeApi()
        result = await fetch_chat(api, 77, "AVITO_CHAT")
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["authorName"], "Candidate")
        self.assertEqual(result[1]["authorName"], "Manager One")
        self.assertEqual(api.calls[1]["after"], "cursor-1")

    async def test_unsupported_channel_identifier_is_rejected(self):
        with self.assertRaises(ValueError):
            chat_query("AVITO_CHAT} mutation Example")

    async def test_denied_is_not_mistaken_for_empty_chat(self):
        class Denied:
            async def query(self, query, variables):
                return {"person": {"id": 1, "communications": {"item": {
                    "__typename": "PersonCommunicationError",
                    "errorType": "ACCESS_DENIED"}}}}
        with self.assertRaisesRegex(ValueError, "ACCESS_DENIED"):
            await fetch_chat(Denied(), 1, "AVITO_CHAT")


if __name__ == "__main__":
    unittest.main()
