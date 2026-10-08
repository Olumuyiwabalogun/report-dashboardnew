from urllib.parse import urlparse

from flask import Flask, abort, jsonify, redirect, render_template, request

import attendance
import auth
import data
import register

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = register.MAX_BYTES + 64 * 1024


@app.before_request
def same_origin_posts():
    """Browsers cache Basic-auth credentials, so refuse form posts that come from other sites."""
    origin = request.headers.get("Origin")
    if request.method == "POST" and origin and urlparse(origin).netloc != request.host:
        abort(403)


app.before_request(auth.gate)


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/summary")
def api_summary():
    return jsonify(data.summarize(data.load_reports()))


@app.get("/api/reports")
def api_reports():
    rows = data.filter_reports(
        data.load_reports(),
        q=request.args.get("q", ""),
        start=request.args.get("from", ""),
        end=request.args.get("to", ""),
    )
    return jsonify([data.public_view(r) for r in rows[:200]])


@app.get("/api/attendance")
def api_attendance():
    scans, skipped = attendance.collect(data.load_reports())
    result = attendance.analyse(scans, request.args.get("from", ""), request.args.get("to", ""))
    result["skipped_reports"] = skipped
    return jsonify(result)


@app.get("/reports/<report_id>/download")
def download(report_id):
    url = data.download_url(report_id, request.args.get("format", "pdf"))
    if not url:
        abort(404)
    return redirect(url)


@app.get("/health")
def health():
    return {"status": "ok"}


def _admin_page(status=200, **context):
    context.setdefault("result", None)
    context.setdefault("error", None)
    return render_template("admin.html", current=register.current(), **context), status


@app.get("/admin")
def admin():
    return _admin_page()


@app.errorhandler(413)
def too_large(_):
    return _admin_page(413, error="That file is too large. The limit is 4 MB.")


@app.post("/admin/register")
def admin_upload():
    file = request.files.get("file")
    if not file or not file.filename.lower().endswith(".xlsx"):
        return _admin_page(400, error="Choose an Excel .xlsx file.")
    blob = file.read()
    try:
        summary = register.inspect(blob)
    except register.RegisterError as err:
        return _admin_page(400, error=str(err))
    scans, _ = attendance.collect(data.load_reports())
    summary.update(register.crosscheck(summary.pop("ids"), scans))
    register.save(blob, summary["children"])
    return _admin_page(result=summary)


@app.get("/admin/children")
def children():
    return render_template("children.html", has_register=register.current() is not None)


@app.get("/admin/api/children")
def api_children():
    return jsonify(register.search(register.load(), request.args.get("q", "")))


@app.get("/admin/children/<child_id>")
def child(child_id):
    record = register.find(register.load(), child_id)
    if not record:
        abort(404)
    scans, _ = attendance.collect(data.load_reports())
    return render_template("child.html", name=register.display_name(record), record=record,
                           stats=attendance.child_history(scans, child_id))
