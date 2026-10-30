"""Read-only Talantix export. Creates Excel/CV ZIP; never sends mutations."""
import argparse
import asyncio
import json
import os
from pathlib import Path
from datetime import datetime, timezone

import httpx
from archive import assemble_archive
from detail_queries import PROFILE, RESUMES, RESUMES_LIGHT, RESPONSES, fetch_section
from communications import fetch_chat
from zipfile import ZipFile

URL = "https://api.talantix.ru/graphql"
PEOPLE = """query($after:String){persons(after:$after,first:50){items{
 id firstName middleName lastName birthDay area{name} source{name}
 contacts{items{type value}}
}pageInfo{hasNextPage endCursor}}}"""
JOBS = """query($after:String){vacancies(after:$after,first:50){items{
 id title department createdAt status salary{from to currency}
}pageInfo{hasNextPage endCursor}}}"""
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
    p.add_argument("--source-archive",default="",help="Reuse base candidate/vacancy listings from a previous ZIP")
    p.add_argument("--limit",type=int,default=0,help="Process first N candidates for API smoke testing; 0 means all")
    args = p.parse_args()
    if args.limit < 0: p.error("--limit must be 0 or positive")
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
            schema = await client.query(SCHEMA)
            save(root/"metadata"/"graphql_schema.json",schema)
            coverage["schema"]={"status":"EXPORTED","count":len(schema.get("__schema",{}).get("types",[]))}
        except (httpx.HTTPError,ValueError,RuntimeError) as error:
            coverage["schema"]={"status":"FAILED","count":0,"details":str(error)}
        if args.schema_only:
            print(coverage["schema"]); return
        if args.source_archive:
            with ZipFile(args.source_archive) as prior:
                old_manifest=json.loads(prior.read("manifest.json"))
                if old_manifest.get("format") != "talantix-export-archive":
                    raise ValueError("Not a Talantix export archive")
                persons=json.loads(prior.read("metadata/persons.json"))
                vacancies=json.loads(prior.read("metadata/vacancies.json"))
        else:
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
        done={"profile":0,"resumes":0,"responses":0,"messages":0,"history":0}
        avito_count = 0
        avito_checked = 0
        avito_supported = True
        if persons:
            try:
                await fetch_chat(client, int(persons[0]["id"]), "AVITO_CHAT")
            except (httpx.HTTPError, ValueError, RuntimeError) as error:
                avito_supported = False
                errors.append(f"AVITO_CHAT probe failed: {error}")
        selected=persons[:args.limit] if args.limit else persons
        for idx,person in enumerate(selected,1):
            pid=str(person["id"])
            candidate_id=int(pid)
            item=dict(person)
            details[pid]=item
            for name,q,fallback in (
                ("profile",PROFILE,""),
                ("resumes",RESUMES,RESUMES_LIGHT),
                ("responses",RESPONSES,""),
            ):
                try:
                    payload=await fetch_section(client,q,candidate_id,fallback)
                    if name=="profile":
                        item.update(payload)
                    else:
                        if name not in payload:
                            raise ValueError("Missing requested section: "+name)
                        item[name]=payload[name]
                    done[name]+=1
                except (httpx.HTTPError,ValueError,RuntimeError,TypeError) as error:
                    errors.append(f"{name}:{pid}: {error}")
            for name,q,target in (("messages",MAIL,messages),("history",HISTORY,histories)):
                try:
                    target[pid]=await pages(client,q,"person",{"id":candidate_id},name)
                    done[name]+=1
                except (httpx.HTTPError,ValueError,RuntimeError,TypeError) as error:
                    errors.append(f"{name}:{pid}: {error}")
            if avito_supported:
                try:
                    chat_messages = await fetch_chat(client, candidate_id, "AVITO_CHAT")
                    existing = messages.get(pid, [])
                    existing_keys = {(str(m.get("channel")), str(m.get("id")))
                                     for m in existing}
                    for chat_msg in chat_messages:
                        key = (str(chat_msg.get("channel")), str(chat_msg.get("id")))
                        if key not in existing_keys:
                            existing.append(chat_msg)
                            existing_keys.add(key)
                            avito_count += 1
                    messages[pid] = existing
                    avito_checked += 1
                except (httpx.HTTPError, ValueError, RuntimeError) as error:
                    errors.append(f"AVITO_CHAT:{pid}: {error}")
            if idx%25==0 or idx==len(selected):
                print("Fetched",idx,"of",len(selected),"candidate details")
        coverage["candidate_details_and_resumes"]={
            "status":"EXPORTED" if done["resumes"]==len(persons) else "PARTIAL",
            "count":done["resumes"],
            "details":f'Candidate profiles: {done["profile"]}; resumes: {done["resumes"]}/{len(persons)}'
        }
        coverage["applications"]={"status":"PARTIAL","count":sum(len((x.get("responses") or {}).get("items") or []) for x in details.values()),
            "details":f'Response requests succeeded for {done["responses"]}/{len(persons)} candidates; original application timestamps not guaranteed.'}
        coverage["messages"]={"status":"PARTIAL","count":sum(map(len,messages.values())),
            "details":f'Email requests succeeded for {done["messages"]}/{len(persons)} candidates; plus available AVITO_CHAT messages.'}
        coverage["history"]={"status":"PARTIAL",
            "count":sum(map(len,histories.values())),
            "details":f'History requests succeeded for {done["history"]}/{len(persons)} candidates; event-specific payload not included.'}
        coverage["original_cv_files"]={"status":"UNSUPPORTED","count":0,
            "details":"Only TXT/JSON CV; downloading original PDF/DOCX not verified."}
        coverage["attachments"]={"status":"UNSUPPORTED","count":0}
        coverage["external_chats"]={
            "status":"PARTIAL" if avito_supported else "NOT_EXPORTED",
            "count":avito_count,
            "details":f'AVITO_CHAT checked for {avito_checked}/{len(persons)} candidates; '
                      'other chat channels are not covered.'
        }
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
