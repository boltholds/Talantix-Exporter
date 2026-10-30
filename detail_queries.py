"""Smaller Talantix GraphQL queries to avoid the API's complexity ceiling.

The previous combined profile/resumes/responses query cost 2600 with a
server-side limit of 2499. Fetch these sections independently.
"""
PROFILE = """query($id:Int!){person(id:$id){... on PersonItem{
 id updatedAt
}}}"""

RESUMES = """query($id:Int!){person(id:$id){... on PersonItem{
 id resumes { items { __typename id name createdAt updatedAt source{type}
 ... on CustomResume { text }
 ... on StructuredResume { title skills link downloadLink apiUrl keySkills }
 }}
}}}"""

RESUMES_LIGHT = """query($id:Int!){person(id:$id){... on PersonItem{
 id resumes { items { __typename id name createdAt updatedAt source{type} } }
}}}"""

RESPONSES = """query($id:Int!){person(id:$id){... on PersonItem{
 id responses { count items { __typename
 ... on ResponseItem { coverLetter updatedAt url
 workflowStatus { ... on WorkflowStatusItem { id name } }
 } } }
}}}"""


def _is_too_complex(error: Exception) -> bool:
    message = str(error).lower()
    return "complexity limit" in message or "permitted complexity" in message


async def fetch_section(client, query: str, candidate_id: int, fallback: str = "") -> dict:
    """Fetch one candidate's section; retry with a lighter query on complexity error."""
    try:
        result = await client.query(query, {"id": candidate_id})
    except ValueError as exc:
        if not fallback or not _is_too_complex(exc):
            raise
        result = await client.query(fallback, {"id": candidate_id})
    person = result.get("person")
    if not isinstance(person, dict) or person.get("id") is None:
        raise ValueError("Talantix did not return the requested candidate")
    if str(person["id"]) != str(candidate_id):
        raise ValueError("Talantix returned a different candidate ID")
    return person
