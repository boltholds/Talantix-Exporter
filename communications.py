"""Read-only Talantix communications via the documented person.communications API."""
from __future__ import annotations

import re

# AVITO_CHAT is confirmed by the official Talantix API example.
DEFAULT_CHANNELS = ("AVITO_CHAT",)

class ChatNotFound(Exception):
    """No chat exists for this candidate and channel."""


class ChatApiError(ValueError):
    """A communications API error other than NOT_FOUND."""


ENUM_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")


def chat_query(channel: str) -> str:
    if not ENUM_RE.fullmatch(channel):
        raise ValueError("Channel must be a GraphQL enum identifier")
    return """query Chat($id:Int!,$after:String){
      person(id:$id){... on PersonItem{
        id communications { item(type:%s,after:$after,first:50) {
          __typename
          ... on AvitoChat {
            count hasUnread pageInfo { hasNextPage endCursor }
            items { __typename id createdAt text
              ... on ThirdPartyChatMessageItem { authorType authorName }
              ... on ManagerChatMessageItem { manager { id firstName lastName } }
            }
          }
          ... on PersonCommunicationError { errorType message }
        }}
      }}
    }""" % channel


async def fetch_chat(client, person_id: int, channel: str) -> list[dict]:
    """Fetch all chat pages; distinguish API errors from genuinely empty chats."""
    query = chat_query(channel)
    cursor: str | None = None
    seen: set[str] = set()
    result: list[dict] = []
    while True:
        data = await client.query(query, {"id": person_id, "after": cursor})
        person = data.get("person") or {}
        if str(person.get("id")) != str(person_id):
            raise ValueError("No matching candidate in communications response")
        communications = person.get("communications")
        if not isinstance(communications, dict):
            raise ValueError("No communications container")
        chat = communications.get("item")
        if not isinstance(chat, dict):
            raise ValueError("No communications item")
        if chat.get("__typename") == "PersonCommunicationError":
            code = str(chat.get("errorType") or "UNKNOWN")
            if code == "NOT_FOUND":
                raise ChatNotFound("No chat for this candidate in channel " + channel)
            raise ChatApiError("Talantix communications error: " + code)
        if "items" not in chat:
            raise ValueError("Communication channel does not expose chat messages")
        for raw in chat.get("items") or []:
            if not isinstance(raw, dict):
                continue
            manager = raw.get("manager") or {}
            result.append({
                "id": raw.get("id"), "createdAt": raw.get("createdAt"),
                "text": raw.get("text"), "channel": channel,
                "type": raw.get("__typename"), "managerId": manager.get("id"),
                "authorType": raw.get("authorType") or ("MANAGER" if manager else ""),
                "authorName": raw.get("authorName") or " ".join(
                    filter(None, [manager.get("firstName"), manager.get("lastName")])),
            })
        info = chat.get("pageInfo") or {}
        if not info.get("hasNextPage"):
            return result
        cursor = info.get("endCursor")
        if not cursor or cursor in seen:
            raise ValueError("Communications cursor missing or repeated")
        seen.add(cursor)
