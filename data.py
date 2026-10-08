"""Data layer: reads Report Studio's own metadata table and signs S3 download links."""
import datetime as dt
import json
import os
import random
from collections import Counter
from decimal import Decimal

DEMO = os.getenv("DEMO_MODE") == "1"
TABLE = os.getenv("TABLE_NAME", "")
BUCKET = os.getenv("BUCKET_NAME", "")
FORMATS = {"pdf": "pdf_key", "xlsx": "excel_key"}


def _demo_reports():
    today = dt.date.today()
    last_sunday = today - dt.timedelta(days=(today.weekday() + 1) % 7)
    items = []
    for i in range(30):
        day = last_sunday - dt.timedelta(weeks=i)
        for n in range(2 if i % 4 == 0 else 1):
            rid = f"demo{i:02d}{n}"
            items.append({
                "report_id": rid, "title": f"Sunday Service {day:%d.%m.%y}",
                "original_name": f"Sunday_Service_{day:%d.%m.%y}.xlsx",
                "created_at": f"{day.isoformat()}T09:{10 + n * 20}:00", "sheet_count": 1,
                "excel_key": f"outputs/{rid}_styled.xlsx", "pdf_key": f"outputs/{rid}_styled.pdf",
            })
    return items


def _demo_doc(report):
    """Fake clock-in sheet for local runs and tests. No real people, columns in odd order."""
    rnd = random.Random(report["report_id"])
    day = report["created_at"][:10]
    rows = []
    for child in range(10, 70):
        if rnd.random() < 0.65:
            arrive = f"{rnd.randint(8, 9):02d}:{rnd.randint(0, 59):02d}"
            rows.append(["Child", str(child), day, arrive])
            if rnd.random() < 0.7:
                rows.append(["Child", str(child), day, f"11:{rnd.randint(0, 25):02d}"])
    rows.append(["Admin", "1", day, "08:00"])
    return {"sheets": [{"header": ["Last Name", "ID", "Date", "Time"], "rows": rows}]}


def fetch_doc(report):
    """Load one report's saved data/<id>_data.json (the parsed spreadsheet rows)."""
    if DEMO:
        return _demo_doc(report)
    import boto3
    body = boto3.client("s3").get_object(Bucket=BUCKET, Key=report["data_key"])["Body"]
    return json.load(body)


def _clean(value):
    return int(value) if isinstance(value, Decimal) else value


def load_reports():
    if DEMO:
        return _demo_reports()
    import boto3
    table = boto3.resource("dynamodb").Table(TABLE)
    resp = table.scan()
    rows = resp["Items"]
    while "LastEvaluatedKey" in resp:  # paginate through the whole table
        resp = table.scan(ExclusiveStartKey=resp["LastEvaluatedKey"])
        rows += resp["Items"]
    cleaned = [{k: _clean(v) for k, v in r.items()} for r in rows]
    return [r for r in cleaned if r.get("created_at")]  # skip malformed rows


def public_view(report):
    """What the browser sees: never the S3 keys."""
    return {
        "report_id": report["report_id"],
        "title": report.get("title", "Untitled"),
        "original_name": report.get("original_name", ""),
        "created_at": report["created_at"],
        "sheet_count": report.get("sheet_count", 0),
        "formats": [f for f, field in FORMATS.items() if report.get(field)],
    }


def filter_reports(reports, q="", start="", end=""):
    q = q.lower().strip()
    out = [
        r for r in reports
        if (not q or q in r.get("title", "").lower() or q in r.get("original_name", "").lower())
        and (not start or r["created_at"][:10] >= start)
        and (not end or r["created_at"][:10] <= end)
    ]
    return sorted(out, key=lambda r: r["created_at"], reverse=True)


def summarize(reports, weeks=12):
    today = dt.date.today()
    monday = today - dt.timedelta(days=today.weekday())
    starts = [monday - dt.timedelta(weeks=n) for n in range(weeks - 1, -1, -1)]
    per_week = Counter()
    for r in reports:
        d = dt.date.fromisoformat(r["created_at"][:10])
        per_week[d - dt.timedelta(days=d.weekday())] += 1
    per_month = Counter(r["created_at"][:7] for r in reports)
    months = sorted(per_month)[-12:]
    cutoff = (today - dt.timedelta(days=30)).isoformat()
    latest = max(reports, key=lambda r: r["created_at"], default=None)
    return {
        "total": len(reports),
        "last_30_days": sum(1 for r in reports if r["created_at"][:10] >= cutoff),
        "total_sheets": sum(r.get("sheet_count", 0) for r in reports),
        "latest": {"title": latest.get("title", "Untitled"), "date": latest["created_at"][:10]}
        if latest else None,
        "by_week": {"labels": [s.strftime("%d %b") for s in starts],
                    "values": [per_week.get(s, 0) for s in starts]},
        "by_month": {"labels": months, "values": [per_month[m] for m in months]},
    }


def download_url(report_id, fmt):
    """Short-lived presigned S3 URL for one report file, or None if unavailable."""
    field = FORMATS.get(fmt)
    if DEMO or not field:
        return None
    import boto3
    table = boto3.resource("dynamodb").Table(TABLE)
    item = table.get_item(Key={"report_id": report_id}).get("Item")
    key = item.get(field) if item else None
    if not key:
        return None
    return boto3.client("s3").generate_presigned_url(
        "get_object", Params={"Bucket": BUCKET, "Key": key}, ExpiresIn=300)
