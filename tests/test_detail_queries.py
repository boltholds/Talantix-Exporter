import unittest
from detail_queries import RESUMES_LIGHT, fetch_section


class FakeClient:
    def __init__(self, fail_first=True):
        self.calls=0
        self.fail_first=fail_first

    async def query(self,query,variables):
        self.calls+=1
        if self.calls==1 and self.fail_first:
            raise ValueError("Requested operation exceeds the permitted complexity limit: 2600 > 2499")
        return {"person":{"id":variables["id"],"resumes":{"items":[]}}}


class TestComplexityFallback(unittest.IsolatedAsyncioTestCase):
    async def test_uses_lighter_query(self):
        api=FakeClient()
        data=await fetch_section(api,"heavy",42,RESUMES_LIGHT)
        self.assertEqual(data["id"],42)
        self.assertEqual(api.calls,2)

    async def test_no_retry_on_unrelated_errors(self):
        class Denied:
            async def query(self,query,variables):
                raise ValueError("access denied")
        with self.assertRaisesRegex(ValueError,"access denied"):
            await fetch_section(Denied(),"heavy",42,RESUMES_LIGHT)

    async def test_wrong_id_is_rejected(self):
        class Wrong:
            async def query(self,query,variables):
                return {"person":{"id":43}}
        with self.assertRaisesRegex(ValueError,"different candidate"):
            await fetch_section(Wrong(),"light",42)


if __name__=="__main__":
    unittest.main()
