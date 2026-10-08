"""Register: validate the machine's user-list export, store it privately, and search it.

The upload page shows only headings and counts. Names appear only on the admin-only
children pages.
"""
import datetime as dt
import hashlib
import io
import os

import openpyxl

import attendance
import data

MAX_BYTES = 4 * 1024 * 1024  # API Gateway and Lambda cap request bodies; base64 adds a third
KEY = "register/current.xlsx"
BUCKET = os.getenv("REGISTER_BUCKET", "")
_demo = {}
_parsed = {}  # ETag -> records, so each stored version is parsed once per warm Lambda


class RegisterError(ValueError):
    """A problem with the uploaded file that the person can fix."""


def _parse(blob):
    """Find the heading row (it may sit below title rows) and read every child."""
    try:
        book = openpyxl.load_workbook(io.BytesIO(blob), read_only=True, data_only=True)
    except Exception:
        raise RegisterError("That file could not be opened as an Excel .xlsx workbook.")
    skip = attendance.excluded_ids()
    header_row, id_col, headers, ids, records, seen = None, None, [], [], [], set()
    for number, row in enumerate(book.worksheets[0].iter_rows(values_only=True), start=1):
        if header_row is None:
            cells = ["" if c is None else str(c).strip() for c in row]
            lowered = [c.lower() for c in cells]
            if "id" in lowered:
                header_row, id_col = number, lowered.index("id")
                headers = [(i, c) for i, c in enumerate(cells) if c]
            elif number >= 20:
                break
            continue
        if id_col >= len(row) or row[id_col] in (None, ""):
            continue
        child = attendance.clean_id(row[id_col])
        ids.append(child)
        if child in skip or child in seen:
            continue
        seen.add(child)
        details = {}
        for i, name in headers:
            if i != id_col and i < len(row) and row[i] not in (None, ""):
                details[name if name not in details else f"{name} ({i + 1})"] = str(row[i]).strip()
        records.append({"id": child, "details": details})
    if header_row is None:
        raise RegisterError("No column named ID was found in the first 20 rows. Export the "
                            "user list from the machine again and include the ID column.")
    return {"header_row": header_row, "columns": [n for _, n in headers],
            "ids": ids, "records": records}


def inspect(blob):
    """Validate an upload. Returns headings and counts only."""
    parsed = _parse(blob)
    registered = {r["id"] for r in parsed["records"]}
    if not registered:
        raise RegisterError("The ID column has no children in it.")
    return {"header_row": parsed["header_row"], "columns": parsed["columns"],
            "children": len(registered), "ids": registered,
            "duplicate_ids": len(parsed["ids"]) - len(set(parsed["ids"]))}


def crosscheck(registered, scans):
    """Compare registered IDs with IDs seen in clock-in reports (counts only)."""
    attended = {child for child, _, _ in scans} - attendance.excluded_ids()
    return {"seen": len(registered & attended), "never_attended": len(registered - attended),
            "not_in_register": len(attended - registered)}


def save(blob, children):
    meta = {"uploaded_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "children": str(children)}
    if data.DEMO:
        _demo.update(meta=meta, blob=blob)
        return
    import boto3
    boto3.client("s3").put_object(Bucket=BUCKET, Key=KEY, Body=blob,
                                  ServerSideEncryption="AES256", Metadata=meta)


def _seed_demo():
    """Sample register (made-up children in families of three) so demo mode works at once."""
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.append(["Name", "ID", "Parent"])
    for i in range(10, 70):
        sheet.append([f"Demo Child {i}", i, f"Demo Parent {(i - 10) // 3}"])
    buf = io.BytesIO()
    book.save(buf)
    _demo.update(meta={"uploaded_at": "sample data (demo mode)", "children": "60"},
                 blob=buf.getvalue())


def _head():
    if data.DEMO:
        if "meta" not in _demo:
            _seed_demo()
        return ({"Metadata": _demo["meta"], "ETag": hashlib.md5(_demo["blob"]).hexdigest()}
                if "meta" in _demo else None)
    import boto3
    from botocore.exceptions import ClientError
    try:
        return boto3.client("s3").head_object(Bucket=BUCKET, Key=KEY)
    except ClientError as err:
        if err.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
            return None
        raise


def current():
    """Details of the stored register, or None if none has been uploaded."""
    head = _head()
    return head["Metadata"] if head else None


def load():
    """Every child in the stored register (admin and teacher IDs removed)."""
    head = _head()
    if not head:
        return []
    version = head["ETag"]
    if version not in _parsed:
        if data.DEMO:
            blob = _demo["blob"]
        else:
            import boto3
            blob = boto3.client("s3").get_object(Bucket=BUCKET, Key=KEY)["Body"].read()
        _parsed.clear()
        _parsed[version] = _parse(blob)["records"]
    return _parsed[version]


def display_name(record):
    fields = {k.lower(): v for k, v in record["details"].items()}
    for key in ("name", "full name", "fullname", "child name"):
        if fields.get(key):
            return fields[key]
    first, last = fields.get("first name", ""), fields.get("last name", "")
    if first or last:
        return f"{first} {last}".strip()
    return next(iter(record["details"].values()), "") or f"ID {record['id']}"


def search(records, query, limit=50):
    """Match any column, so a parent's name or surname lists the whole family."""
    query = query.lower().strip()
    if len(query) < 2:
        return {"results": [], "total": 0}
    hits = [r for r in records
            if query in r["id"].lower() or any(query in v.lower() for v in r["details"].values())]
    hits.sort(key=lambda r: display_name(r).lower())
    return {"results": [{"id": r["id"], "name": display_name(r)} for r in hits[:limit]],
            "total": len(hits)}


def find(records, child_id):
    return next((r for r in records if r["id"] == child_id), None)
