from __future__ import annotations

import io
import os
import re
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from secrets import token_urlsafe
from threading import Lock

from flask import Flask, render_template, request, send_file, url_for
from werkzeug.utils import secure_filename

from nyc_data import NYCPropertyClient, PropertyRecord
from workbook_io import build_result_zip, read_addresses


app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024
app.config["SECRET_KEY"] = os.environ.get("FLASK_SECRET_KEY", "local-only")

_RESULT_CACHE: OrderedDict[str, tuple[bytes, str]] = OrderedDict()
_RESULT_LOCK = Lock()
_MAX_CACHED_RESULTS = 5


def _store_result(payload: bytes, download_name: str) -> str:
    result_id = token_urlsafe(12)
    with _RESULT_LOCK:
        _RESULT_CACHE[result_id] = (payload, download_name)
        while len(_RESULT_CACHE) > _MAX_CACHED_RESULTS:
            _RESULT_CACHE.popitem(last=False)
    return result_id


def _render_result(records: list[PropertyRecord], payload: bytes, download_name: str):
    result_id = _store_result(payload, download_name)
    failures = []
    for record in records:
        if not record.bin or record.co_download_status == "Downloaded":
            continue
        failures.append({
            "address": record.input_address,
            "bin": record.bin,
            "status": record.co_download_status,
            "manual_url": record.co_retry_url,
            "retry_url": url_for(
                "retry_co_download",
                bin=record.bin,
                address=record.input_address,
            ),
        })
    return render_template(
        "index.html",
        result={
            "download_url": url_for("download_result", result_id=result_id),
            "download_name": download_name,
            "failed_co": failures,
            "record_count": len(records),
        },
    )


@app.get("/")
def index():
    return render_template("index.html")


@app.post("/process")
def process_workbook():
    upload = request.files.get("workbook")
    if not upload or not upload.filename:
        return render_template("index.html", error="请选择一个 Excel 文件。"), 400
    if Path(upload.filename).suffix.lower() not in {".xlsx", ".xlsm"}:
        return render_template("index.html", error="目前只支持 .xlsx 或 .xlsm 文件。"), 400

    try:
        content = upload.read()
        addresses, source = read_addresses(content, request.form.get("column", "Jobsite"))
    except ValueError as exc:
        return render_template("index.html", error=str(exc)), 400

    if not addresses:
        return render_template("index.html", error="没有找到非空地址。"), 400

    worker_count = max(1, min(int(os.environ.get("LOOKUP_WORKERS", "4")), 8))

    def lookup_one(address: str):
        # A separate Session per worker avoids sharing mutable HTTP state.
        return NYCPropertyClient().lookup(address)

    records = []
    documents: dict[str, list[tuple[str, bytes]]] = {}
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        for address, (record, files) in zip(addresses, executor.map(lookup_one, addresses)):
            records.append(record)
            if files:
                documents[address] = files

    output = build_result_zip(records, documents, source)
    download_name = f"{secure_filename(Path(upload.filename).stem) or 'property'}_results.zip"
    return _render_result(records, output, download_name)


@app.get("/download-result/<result_id>")
def download_result(result_id: str):
    with _RESULT_LOCK:
        cached = _RESULT_CACHE.get(result_id)
        if cached:
            _RESULT_CACHE.move_to_end(result_id)
    if not cached:
        return render_template("index.html", error="This result expired. Please process the workbook again."), 404
    payload, download_name = cached
    return send_file(
        io.BytesIO(payload),
        mimetype="application/zip",
        as_attachment=True,
        download_name=download_name,
    )


@app.get("/retry-co")
def retry_co_download():
    bin_number = (request.args.get("bin") or "").strip()
    address = (request.args.get("address") or f"BIN {bin_number}").strip()
    if not re.fullmatch(r"\d{7}", bin_number):
        return render_template("index.html", error="Invalid seven-digit NYC BIN for CO retry."), 400

    record, files = NYCPropertyClient().download_co(address, bin_number)
    documents = {address: files} if files else {}
    output = build_result_zip([record], documents, f"CO retry for BIN {bin_number}")
    return _render_result([record], output, f"co_retry_{bin_number}.zip")


@app.errorhandler(413)
def too_large(_error):
    return render_template("index.html", error="文件超过 20 MB。"), 413


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)