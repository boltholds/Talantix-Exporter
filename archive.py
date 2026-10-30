"""Create a ZIP containing an Excel workbook, candidate CV folders and a manifest."""
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

from xlsx_portable import write_xlsx

SHEETS = {
    "Candidates": ["candidate_id","first_name","middle_name","last_name","full_name","birth_date_utc","area","source","updated_at_utc","emails","phones","other_contacts"],
    "Contacts": ["candidate_id","contact_type","contact_value"],
    "CV": ["candidate_id","resume_id","resume_type","title","created_at_utc","updated_at_utc","source_type","cv_path","metadata_path","file_status"],
    "Vacancies": ["vacancy_id","title","department","status","created_at_utc","salary_from","salary_to","currency"],
    "Applications": ["candidate_id","vacancy_id","status_id","status_name","updated_at_utc","applied_at_utc","cover_letter","response_url"],
    "Messages": ["candidate_id","message_id","created_at_utc","channel","type","status","sender_id","author_type","author_name","subject","text","content_path"],
    "History": ["candidate_id","event_id","event_type","event_time_utc"],
    "Export_Info": ["section","status","record_count","details"],
}
MAX_CELL = 32767

def utc(value):
    if value is None or value == "":
        return ""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            # Talantix timestamp fields use Unix epoch milliseconds.
            # Epoch arithmetic works for dates before 1970 on Windows,
            # unlike datetime.fromtimestamp() for negative timestamps.
            epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
            return (epoch + timedelta(milliseconds=value)).isoformat()
        except (OverflowError, ValueError, OSError):
            # Preserve the original value for later reconciliation.
            # Do not fail the entire export on one malformed timestamp.
            return str(value)
    return str(value)

def safe_text(value):
    if value is None:
        return ""
    text = str(value)
    if text.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + text
    return text

def items(value):
    return value.get("items") or [] if isinstance(value, dict) else []

def save_resume(root, person_id, resume):
    resume_id = str(resume.get("id","unknown"))
    folder = root / "CV" / person_id
    folder.mkdir(parents=True, exist_ok=True)
    base = "resume_" + resume_id
    raw = folder / (base + ".json")
    raw.write_text(json.dumps(resume, ensure_ascii=False, indent=2), encoding="utf8")
    metadata_path = raw.relative_to(root).as_posix()
    text = resume.get("text") or ""
    if not text:
        text = "\n".join(f"{key}: {value}" for key,value in resume.items() if key in ("name","title","skills","keySkills") and value)
    if text:
        path = folder / (base + ".txt")
        path.write_text(str(text), encoding="utf8")
        return path.relative_to(root).as_posix(), metadata_path, "TEXT_EXPORTED_ORIGINAL_UNAVAILABLE"
    return "", metadata_path, "METADATA_ONLY_ORIGINAL_UNAVAILABLE"

def records(root, persons, vacancies, details, histories, messages, coverage, *, existing_cv_names=None):
    rows = {key: [] for key in SHEETS}
    stage_ids = {}
    for vacancy in vacancies:
        vid = str(vacancy["id"])
        salary = vacancy.get("salary") or {}
        rows["Vacancies"].append([vid,vacancy.get("title"),vacancy.get("department"),vacancy.get("status"),utc(vacancy.get("createdAt")),salary.get("from"),salary.get("to"),salary.get("currency")])
        for status in items(vacancy.get("workflowStatuses")):
            if status.get("id") is not None:
                stage_ids[str(status["id"])] = vid
    for person in persons:
        pid = str(person["id"])
        p = details.get(pid) or person
        contacts = items(p.get("contacts"))
        emails = [str(c.get("value","")) for c in contacts if c.get("type") == "email"]
        phones = [str(c.get("value","")) for c in contacts if c.get("type") in ("cell","phone")]
        other = [f"{c.get('type')}:{c.get('value')}" for c in contacts if c.get("type") not in ("cell","phone","email")]
        name = " ".join(filter(None,[p.get("lastName"),p.get("firstName"),p.get("middleName")]))
        rows["Candidates"].append([pid,p.get("firstName"),p.get("middleName"),p.get("lastName"),name,utc(p.get("birthDay")),(p.get("area") or {}).get("name"),(p.get("source") or {}).get("name"),utc(p.get("updatedAt")),"; ".join(emails),"; ".join(phones),"; ".join(other)])
        for contact in contacts:
            rows["Contacts"].append([pid,contact.get("type"),contact.get("value")])
        for resume in items(p.get("resumes")):
            if existing_cv_names is None:
                cv_path, metadata_path, state = save_resume(root,pid,resume)
            else:
                resume_id = str(resume.get("id", "unknown"))
                prefix = f"CV/{pid}/resume_{resume_id}"
                candidate_metadata = prefix + ".json"
                candidate_text = prefix + ".txt"
                metadata_path = candidate_metadata if candidate_metadata in existing_cv_names else ""
                cv_path = candidate_text if candidate_text in existing_cv_names else ""
                state = ("TEXT_EXPORTED_ORIGINAL_UNAVAILABLE" if cv_path
                         else "METADATA_ONLY_ORIGINAL_UNAVAILABLE" if metadata_path
                         else "MISSING_FILE")
            rows["CV"].append([pid,str(resume.get("id","")),resume.get("__typename"),resume.get("name"),utc(resume.get("createdAt")),utc(resume.get("updatedAt")),(resume.get("source") or {}).get("type"),cv_path,metadata_path,state])
        for response in items(p.get("responses")):
            if response.get("__typename") != "ResponseItem":
                continue
            status = response.get("workflowStatus") or {}
            sid = str(status.get("id",""))
            rows["Applications"].append([pid,stage_ids.get(sid,""),sid,status.get("name"),utc(response.get("updatedAt")),"",response.get("coverLetter"),response.get("url")])
        for message in messages.get(pid, []):
            mid = str(message.get("id") or "")
            body = str(message.get("text") or "")
            full_path = ""
            if len(body) > MAX_CELL:
                path = root / "Messages" / pid / (hashlib.sha256(mid.encode()).hexdigest()[:20] + ".txt")
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(body,encoding="utf8")
                full_path = path.relative_to(root).as_posix()
            rows["Messages"].append([pid,mid,utc(message.get("createdAt")),message.get("channel"),message.get("type"),message.get("status"),message.get("managerId"),message.get("authorType"),message.get("authorName"),message.get("subject"),body[:MAX_CELL] if not full_path else "[See text file]",full_path])
        for event in histories.get(pid,[]):
            rows["History"].append([pid,str(event.get("id","")),event.get("__typename"),utc(event.get("eventTime"))])
    for section, info in coverage.items():
        rows["Export_Info"].append([section,info.get("status","UNKNOWN"),info.get("count",0),info.get("details","")])
    return rows

def write_workbook(root, rows):
    path = root / "Talantix_Export.xlsx"
    write_xlsx(path, {name: (headers, rows[name]) for name,headers in SHEETS.items()})
    return path

def assemble_archive(stage, output, persons, vacancies, details, histories, messages, coverage):
    stage = Path(stage)
    output = Path(output).resolve()
    stage.mkdir(parents=True,exist_ok=True)
    rows = records(stage,persons,vacancies,details,histories,messages,coverage)
    write_workbook(stage,rows)
    manifest = {
        "format":"talantix-export-archive","version":"1.0",
        "source":"Talantix GraphQL API",
        "exported_at_utc":datetime.now(timezone.utc).isoformat(),
        "counts":{name:len(row) for name,row in rows.items()},
        "coverage":coverage,
        "note":"Original PDF/DOCX CV and message attachments are NOT claimed as exported.",
    }
    (stage/"manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding="utf8")
    output.parent.mkdir(parents=True,exist_ok=True)
    temp = output.with_suffix(output.suffix + ".tmp")
    with ZipFile(temp,"w",ZIP_DEFLATED,compresslevel=6) as z:
        for f in sorted(stage.rglob("*")):
            if f.is_file() and f.resolve() not in (temp,output):
                z.write(f,f.relative_to(stage).as_posix())
    temp.replace(output)
    with ZipFile(output) as z:
        if z.testzip() or "Talantix_Export.xlsx" not in z.namelist():
            raise RuntimeError("ZIP integrity check failed")
    return output
