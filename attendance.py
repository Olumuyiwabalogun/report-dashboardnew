"""Attendance analytics from Report Studio's saved data/<id>_data.json files.

Privacy: only the ID, Date and Time columns are read. Names are never parsed or kept.
A scan before CLOCK_OUT_FROM (default 11:00) is a clock-in, at or after it a clock-out.
"""
import os
import re
import statistics
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

import data

_TIME = re.compile(r"(\d{1,2}):(\d{2})")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_cache = {}  # report_id -> scans. A built report never changes, so caching is safe.


def excluded_ids():
    return {x.strip() for x in os.getenv("EXCLUDE_IDS", "1").split(",") if x.strip()}


def clock_out_from():
    hour, minute = os.getenv("CLOCK_OUT_FROM", "11:00").split(":")
    return int(hour) * 60 + int(minute)


def clean_id(value):
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).strip()


def parse_doc(doc):
    """One saved report -> [(child_id, 'YYYY-MM-DD', minutes_after_midnight)]."""
    scans = []
    for sheet in doc.get("sheets", []):
        header = [str(h).strip().lower() for h in sheet.get("header", [])]
        try:
            i_id, i_date, i_time = header.index("id"), header.index("date"), header.index("time")
        except ValueError:
            raise ValueError(f"no ID/Date/Time columns (found: {header})")
        for row in sheet.get("rows", []):
            if len(row) <= max(i_id, i_date, i_time):
                continue
            child, day, clock = clean_id(row[i_id]), _DATE.search(str(row[i_date])), \
                _TIME.search(str(row[i_time]))
            if child and day and clock:
                scans.append((child, day.group(), int(clock.group(1)) * 60 + int(clock.group(2))))
    return scans


def _scans_for(report):
    rid = report["report_id"]
    if rid not in _cache:
        _cache[rid] = parse_doc(data.fetch_doc(report))
    return _cache[rid]


def collect(reports):
    """Scans from every report. A report that can't be read is counted, not fatal."""
    def work(report):
        try:
            return _scans_for(report)
        except Exception:
            return None

    scans, skipped = [], 0
    with ThreadPoolExecutor(8) as pool:
        for result in pool.map(work, reports):
            if result is None:
                skipped += 1
            else:
                scans.extend(result)
    return scans, skipped


def _hhmm(minutes):
    minutes = int(minutes)
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def analyse(scans, start="", end=""):
    """Per-service-date attendance. The unit is the date, not the report, so two reports
    for the same Sunday never double-count a child."""
    cut, skip = clock_out_from(), excluded_ids()
    by_date = defaultdict(lambda: defaultdict(set))
    for child, day, minute in scans:
        if child in skip or (start and day < start) or (end and day > end):
            continue
        by_date[day][child].add(minute)

    services, everyone = [], set()
    for day in sorted(by_date):
        both = only_in = only_out = 0
        arrivals = []
        for child, times in by_date[day].items():
            ins = [t for t in times if t < cut]
            outs = [t for t in times if t >= cut]
            both += bool(ins and outs)
            only_in += bool(ins and not outs)
            only_out += bool(outs and not ins)
            if ins:
                arrivals.append(min(ins))
        everyone.update(by_date[day])
        services.append({
            "date": day, "present": len(by_date[day]), "in_and_out": both,
            "in_only": only_in, "out_only": only_out,
            "median_arrival": _hhmm(statistics.median(arrivals)) if arrivals else None,
        })

    totals = {"services": len(services), "unique_children": len(everyone),
              "average_present": round(statistics.mean(s["present"] for s in services))
              if services else 0,
              "busiest": max(services, key=lambda s: s["present"]) if services else None}
    return {"services": services, "totals": totals}


def child_history(scans, child_id):
    """One child's record measured against every service date seen in the reports."""
    cut, skip = clock_out_from(), excluded_ids()
    dates = sorted({day for child, day, _ in scans if child not in skip})
    times = defaultdict(set)
    for child, day, minute in scans:
        if child == child_id:
            times[day].add(minute)
    rows = []
    for day in dates:
        seen = times.get(day, set())
        ins = sorted(m for m in seen if m < cut)
        outs = sorted(m for m in seen if m >= cut)
        rows.append({"date": day, "present": bool(seen),
                     "arrived": _hhmm(ins[0]) if ins else None,
                     "left": _hhmm(outs[-1]) if outs else None})
    attended = sum(r["present"] for r in rows)
    missed = 0
    for row in reversed(rows):
        if row["present"]:
            break
        missed += 1
    return {"services_held": len(rows), "attended": attended,
            "rate": round(100 * attended / len(rows)) if rows else 0,
            "missed_in_a_row": missed,
            "last_seen": next((r["date"] for r in reversed(rows) if r["present"]), None),
            "history": list(reversed(rows))[:26]}
