import os

os.environ["DEMO_MODE"] = "1"

import auth  # noqa: E402
import data  # noqa: E402
from app import app  # noqa: E402

client = app.test_client()


def test_health():
    assert client.get("/health").json == {"status": "ok"}


def test_summary_shape():
    body = client.get("/api/summary").json
    assert body["total"] == len(data.load_reports())
    assert len(body["by_week"]["labels"]) == 12
    assert body["latest"]["title"].startswith("Sunday Service")


def test_search_by_title():
    rows = client.get("/api/reports?q=sunday").json
    assert rows and all("sunday" in r["title"].lower() for r in rows)


def test_search_no_match():
    assert client.get("/api/reports?q=zzzz").json == []


def test_date_range_filter():
    everything = data.load_reports()
    newest = max(r["created_at"][:10] for r in everything)
    rows = client.get(f"/api/reports?from={newest}").json
    assert rows and all(r["created_at"][:10] >= newest for r in rows)


def test_api_never_exposes_s3_keys():
    row = client.get("/api/reports").json[0]
    assert "excel_key" not in row and "pdf_key" not in row
    assert set(row["formats"]) == {"pdf", "xlsx"}


def test_download_unknown_is_404():
    assert client.get("/reports/nope/download?format=pdf").status_code == 404


def test_index_page_renders():
    assert b"Report dashboard" in client.get("/").data


def test_auth_blocks_without_password(monkeypatch):
    monkeypatch.setenv("DASH_PASSWORD_PARAM", "/x")
    monkeypatch.setattr(auth, "_password", lambda param: "s3cret")
    assert client.get("/api/reports").status_code == 401
    assert client.get("/health").status_code == 200


def test_auth_accepts_correct_password(monkeypatch):
    monkeypatch.setenv("DASH_PASSWORD_PARAM", "/x")
    monkeypatch.setattr(auth, "_password", lambda param: "s3cret")
    ok = {"Authorization": "Basic dXNlcjpzM2NyZXQ="}   # user:s3cret
    bad = {"Authorization": "Basic dXNlcjp3cm9uZw=="}  # user:wrong
    assert client.get("/api/reports", headers=ok).status_code == 200
    assert client.get("/api/reports", headers=bad).status_code == 401


def test_auth_fails_closed_when_password_unreadable(monkeypatch):
    monkeypatch.setenv("DASH_PASSWORD_PARAM", "/x")

    def boom(param):
        raise RuntimeError("ssm down")
    monkeypatch.setattr(auth, "_password", boom)
    assert client.get("/api/reports").status_code == 503


# ---------- attendance (fake data only) ----------
import attendance  # noqa: E402

DOC = {"sheets": [{"header": ["Name", "ID", "Date", "Time"], "rows": [
    ["A", "5", "2026-09-13", "09:20"], ["A", "5", "2026-09-13", "11:10"],
    ["B", 6, "2026-09-13", "09:30"], ["B", "6", "2026-09-13", "09:30"],
    ["C", "7", "2026-09-13", "11:15"], ["Admin", "1", "2026-09-13", "08:00"]]}]}


def test_parse_finds_columns_by_header_name():
    scans = attendance.parse_doc(DOC)
    assert ("5", "2026-09-13", 560) in scans and ("6", "2026-09-13", 570) in scans


def test_parse_rejects_sheet_without_required_columns():
    bad = {"sheets": [{"header": ["Name", "Class"], "rows": []}]}
    try:
        attendance.parse_doc(bad)
    except ValueError as err:
        assert "ID/Date/Time" in str(err)
    else:
        raise AssertionError("expected ValueError")


def test_in_out_rule_and_admin_excluded():
    day = attendance.analyse(attendance.parse_doc(DOC))["services"][0]
    assert (day["present"], day["in_and_out"], day["in_only"], day["out_only"]) == (3, 1, 1, 1)
    assert day["median_arrival"] == "09:25"


def test_two_reports_same_date_do_not_double_count():
    scans = attendance.parse_doc(DOC) + attendance.parse_doc(DOC)
    assert attendance.analyse(scans)["services"][0]["present"] == 3


def test_period_filter():
    scans = attendance.parse_doc(DOC)
    assert attendance.analyse(scans, start="2026-09-14")["services"] == []


def test_attendance_endpoint_one_row_per_service_date():
    body = client.get("/api/attendance").json
    assert body["skipped_reports"] == 0
    assert body["totals"]["services"] == 30  # some Sundays have two reports; merged by date
    assert "name" not in str(body).lower()


# ---------- register upload (fake children only) ----------
import base64  # noqa: E402
import io  # noqa: E402

import openpyxl  # noqa: E402

import register  # noqa: E402


def make_xlsx(header=("First Name", "ID", "Department"), ids=range(10, 80), title_rows=7):
    book = openpyxl.Workbook()
    sheet = book.active
    for i in range(title_rows):
        sheet.append([f"machine export line {i}"])
    sheet.append(list(header))
    for i in ids:
        sheet.append([f"Fake Child {i}", i, "Kids"])
    sheet.append(["Fake Duplicate", 10, "Kids"])
    sheet.append(["Admin", 1, "-"])
    buf = io.BytesIO()
    book.save(buf)
    return buf.getvalue()


def upload(blob, name="register.xlsx", headers=None):
    return client.post("/admin/register", headers=headers or {},
                       data={"file": (io.BytesIO(blob), name)}, content_type="multipart/form-data")


def test_upload_valid_register_shows_counts_not_names():
    resp = upload(make_xlsx())
    text = resp.get_data(as_text=True)
    assert resp.status_code == 200 and "Upload saved" in text
    assert "Fake Child" not in text  # names are never echoed
    assert register.current()["children"] == "70"
    assert "heading row 8" in text   # header found below the title rows


def test_crosscheck_counts_children_who_never_attended():
    scans = [("10", "2026-09-13", 560), ("999", "2026-09-13", 560)]
    result = register.crosscheck({"10", "11"}, scans)
    assert result == {"seen": 1, "never_attended": 1, "not_in_register": 1}


def test_upload_without_id_column_is_rejected():
    resp = upload(make_xlsx(header=("First Name", "Class", "Department")))
    assert resp.status_code == 400 and b"No column named ID" in resp.data


def test_upload_rejects_non_excel():
    assert upload(b"not a workbook", name="register.xlsx").status_code == 400
    assert upload(make_xlsx(), name="register.csv").status_code == 400


def test_cross_site_post_is_refused():
    assert upload(make_xlsx(), headers={"Origin": "https://evil.example"}).status_code == 403


def _basic(password):
    return {"Authorization": "Basic " + base64.b64encode(f"u:{password}".encode()).decode()}


def test_viewer_password_cannot_open_admin(monkeypatch):
    monkeypatch.setenv("DASH_PASSWORD_PARAM", "/v")
    monkeypatch.setenv("ADMIN_PASSWORD_PARAM", "/a")
    monkeypatch.setattr(auth, "_password", lambda param: {"/v": "view", "/a": "adm"}[param])
    assert client.get("/admin", headers=_basic("view")).status_code == 401
    assert client.get("/admin", headers=_basic("adm")).status_code == 200
    assert client.get("/api/reports", headers=_basic("view")).status_code == 200


def test_admin_fails_closed_without_admin_password(monkeypatch):
    monkeypatch.setenv("DASH_PASSWORD_PARAM", "/v")
    monkeypatch.delenv("ADMIN_PASSWORD_PARAM", raising=False)
    assert client.get("/admin", headers=_basic("view")).status_code == 503


# ---------- child search and history (fake children only) ----------
def upload_fake(names=None):
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.append(["Name", "ID", "Parent"])
    names = names or {"10": "Fake Amara", "11": "Fake Bayo", "12": "Fake Amara Jr"}
    for child_id, name in names.items():
        sheet.append([name, int(child_id), "Fake Parent Okoro"])
    buf = io.BytesIO()
    book.save(buf)
    assert upload(buf.getvalue()).status_code == 200


def test_search_by_child_and_by_parent():
    upload_fake()
    amara = client.get("/admin/api/children?q=amara").json
    assert [c["id"] for c in amara["results"]] == ["10", "12"]
    family = client.get("/admin/api/children?q=okoro").json
    assert family["total"] == 3          # a parent's name lists the whole family
    assert client.get("/admin/api/children?q=a").json["results"] == []   # too short
    assert client.get("/admin/api/children?q=zzzz").json["total"] == 0


def test_child_page_shows_details_and_history():
    upload_fake()
    page = client.get("/admin/children/10")
    text = page.get_data(as_text=True)
    assert page.status_code == 200 and "Fake Amara" in text and "Fake Parent Okoro" in text
    assert "Attended" in text and "Service date" in text
    assert client.get("/admin/children/99999").status_code == 404


def test_names_are_escaped_in_pages():
    upload_fake({"10": "<b>Fake</b>"})
    text = client.get("/admin/children/10").get_data(as_text=True)
    assert "&lt;b&gt;Fake" in text and "<b>Fake</b>" not in text


def test_new_upload_replaces_search_results():
    upload_fake({"10": "Fake Old"})
    upload_fake({"10": "Fake New"})
    assert client.get("/admin/api/children?q=fake").json["results"][0]["name"] == "Fake New"


def test_child_history_counts_missed_services():
    scans = [("5", "2026-09-06", 560), ("5", "2026-09-06", 670),
             ("9", "2026-09-13", 560), ("9", "2026-09-20", 560)]
    h = attendance.child_history(scans, "5")
    assert (h["services_held"], h["attended"], h["rate"]) == (3, 1, 33)
    assert h["missed_in_a_row"] == 2 and h["last_seen"] == "2026-09-06"
    assert h["history"][-1] == {"date": "2026-09-06", "present": True,
                                "arrived": "09:20", "left": "11:10"}


def test_children_pages_need_admin_password(monkeypatch):
    monkeypatch.setenv("DASH_PASSWORD_PARAM", "/v")
    monkeypatch.setenv("ADMIN_PASSWORD_PARAM", "/a")
    monkeypatch.setattr(auth, "_password", lambda param: {"/v": "view", "/a": "adm"}[param])
    for path in ("/admin/children", "/admin/api/children?q=amara", "/admin/children/10"):
        assert client.get(path, headers=_basic("view")).status_code == 401
    assert client.get("/admin/children", headers=_basic("adm")).status_code == 200


def test_demo_mode_starts_with_a_searchable_sample_register():
    register._demo.clear()
    assert client.get("/admin/api/children?q=demo child 12").json["total"] == 1
    assert client.get("/admin/api/children?q=demo parent 0").json["total"] == 3
    assert client.get("/admin/children/12").status_code == 200
