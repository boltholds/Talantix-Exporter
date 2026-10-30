"""Read-only Talantix export. Creates Excel/CV ZIP; never sends mutations."""
import argparse
import asyncio
import json
import os
from pathlib import Path
from datetime import datetime, timezone

import httpx
from archive import assemble_archive

URL = "https://api.talantix.ru/graphql"
PEOPLE = """query($after:String){persons(after:$after,first:50){items{
 id firstName middleName lastName birthDay area{name} source{name}
 contacts{items{type value}}
}pageInfo{hasNextPage endCursor}}}"""
JOBS = """query($after:String){vacancies(after:$after,first:50){items{
 id title department createdAt status salary{from to currency}
}pageInfo{hasNextPage endCursor}}}"""
DETAIL = """query($id:Int!){person(id:$id){... on PersonItem{
 id firstName middleName lastName updatedAt birthDay area{name} source{name}
 contacts{items{type value}}
 resumes{items{__typename id name createdAt updatedAt source{type}
 ... on CustomResume{text}
 ... on StructuredResume{title skills link}
 }}
 responses{items{__typename ... on ResponseItem{
 coverLetter updatedAt url workflowStatus{... on WorkflowStatusItem{id name}}
 }}}
}}}"""
STAGES = """query($id:Int!){vacancy(id:$id){... on VacancyItem{
 workflowStatuses{items{id name}}
}}}"""
MAIL = """query($id:Int!,$after:String){person(id:$id){... on PersonItem{
 messages(after:$after,first:50){items{
 id createdAt channel type status subject text managerId
 }pageInfo{hasNextPage endCursor}}
}}}"""
HISTORY = """query($id:Int!,$after:String){person(id:$id){... on PersonItem{
 history(after:$after,first:50){items{__typename id eventTime}
 pageInfo{hasNextPage endCursor}}
}}}"""
SCHEMA = """query{__schema{queryType{name} types{kind name}}}"""

def save(path, value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding="utf8")

class Client:
    def __init__(self,token,agent):
        self.http = httpx.AsyncClient(timeout=90,headers={
            "Authorization":"Bearer "+token, "User-Agent":agent,
            "Content-Type":"application/json"})
    async def close(self):
        await self.http.aclose()
    async def query(self,q,vars=None,schema=False):
        endpoint = URL + ("/schema" if schema else "")
        for n in range(6):
            try:
                response = await self.http.post(endpoint,json={"query":q,"variables":vars or {}})
                if response.status_code in (429,500,502,503,504) and n<5:
                    await asyncio.sleep(min(30,2**n))
                    continue
                response.raise_for_status()
                result = response.json()
                if result.get("errors"):
                    raise ValueError(str(result["errors"])[:1200])
                return result["data"]
            except (httpx.TimeoutException,httpx.TransportError):
                if n==5: raise
                await asyncio.sleep(min(30,2**n))
        raise RuntimeError("API retry limit reached")

async def pages(client,q,key,vars=None,subkey=None):
    cursor = None
    seen = set()
    result = []
    while True:
        data = await client.query(q,dict(vars or {},after=cursor))
        node = data[key] or {}
        coll = (node.get(subkey) or {}) if subkey else node
        result.extend(coll.get("items") or [])
        page = coll.get("pageInfo") or {}
        if not page.get("hasNextPage"): return result
        cursor = page.get("endCursor")
        if not cursor or cursor in seen:
            raise ValueError("Incomplete pagination: "+(subkey or key))
        seen.add(cursor)

async def run():
    p = argparse.ArgumentParser()
    p.add_argument("command",nargs="?",choices=["export"],default="export")
    p.add_argument("--output",default="Talantix_Export.zip")
    p.add_argument("--workdir",default="")
    p.add_argument("--schema-only",action="store_true")
    args = p.parse_args()
    token = os.environ.get("TALANTIX_TOKEN","")
    if not token: p.error("Set TALANTIX_TOKEN environment variable")
    root = Path(args.workdir) if args.workdir else Path(".talantix_work") / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%f")
    if root.exists() and any(root.iterdir()):
        p.error("Workdir must be empty: choose a new --workdir for each export")
    root.mkdir(parents=True,exist_ok=True)
    client = Client(token,os.environ.get("TALANTIX_USER_AGENT","Persona AI Talantix Exporter"))
    coverage = {}
    try:
        try:
            schema = await client.query(SCHEMA,schema=True)
            save(root/"metadata"/"graphql_schema.json",schema)
            coverage["schema"]={"status":"EXPORTED","count":len(schema.get("__schema",{}).get("types",[]))}
        except (httpx.HTTPError,ValueError,RuntimeError) as error:
            coverage["schema"]={"status":"FAILED","count":0,"details":str(error)}
        if args.schema_only:
            print(coverage["schema"]); return
        persons = await pages(client,PEOPLE,"persons")
        vacancies = await pages(client,JOBS,"vacancies")
        coverage["candidates"]={"status":"EXPORTED","count":len(persons)}
        coverage["vacancies"]={"status":"EXPORTED","count":len(vacancies)}
        details,histories,messages,errors={},{},{},[]
        for v in vacancies:
            try:
                res = await client.query(STAGES,{"id":int(v["id"])})
                v["workflowStatuses"]=(res.get("vacancy") or {}).get("workflowStatuses")
            except (httpx.HTTPError,ValueError,RuntimeError) as error:
                errors.append(f"vacancy:{v['id']}: {error}")
        for idx,person in enumerate(persons,1):
            pid=str(person["id"])
            try:
                res=await client.query(DETAIL,{"id":int(pid)})
                item=res.get("person") or {}
                if not item.get("id"): raise ValueError("No detail")
                details[pid]=item
            except (httpx.HTTPError,ValueError,RuntimeError) as error:
                errors.append(f"candidate:{pid}: {error}")
                continue
            for name,q,target in (("messages",MAIL,messages),("history",HISTORY,histories)):
                try:
                    target[pid]=await pages(client,q,"person",{"id":int(pid)},name)
                except (httpx.HTTPError,ValueError,RuntimeError) as error:
                    errors.append(f"{name}:{pid}: {error}")
            if idx%25==0: print("Fetched",idx,"of",len(persons))
        coverage["candidate_details_and_resumes"]={
            "status":"EXPORTED" if len(details)==len(persons) else "PARTIAL",
            "count":len(details)}
        coverage["applications"]={"status":"PARTIAL","count":sum(len((x.get("responses") or {}).get("items") or []) for x in details.values()),
            "details":"Original application timestamps not guaranteed; cover letters/stages included."}
        coverage["messages"]={"status":"PARTIAL","count":sum(map(len,messages.values())),
            "details":"Email messages only, not all external chats."}
        coverage["history"]={"status":"PARTIAL",
            "count":sum(map(len,histories.values())),
            "details":"Event ID/type/time only; event-specific payload not yet exported"}
        coverage["original_cv_files"]={"status":"UNSUPPORTED","count":0,
            "details":"Only TXT/JSON CV; downloading original PDF/DOCX not verified."}
        coverage["attachments"]={"status":"UNSUPPORTED","count":0}
        coverage["external_chats"]={"status":"NOT_EXPORTED","count":0}
        coverage["errors"]={"status":"FAILED" if errors else "EXPORTED","count":len(errors),
            "details":"metadata/errors.json"}
        for filename,value in (
            ("errors",errors),("persons",persons),("vacancies",vacancies),
            ("candidate_details",details),("messages",messages),("history",histories)):
            save(root/"metadata"/(filename+".json"),value)
        path=assemble_archive(root,Path(args.output),persons,vacancies,details,histories,messages,coverage)
        print("Created:",path)
    finally:
        await client.close()

if __name__=="__main__":
    asyncio.run(run())
