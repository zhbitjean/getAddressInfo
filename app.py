from __future__ import annotations
import io, os, re, tempfile, zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.background import BackgroundTask
from werkzeug.utils import secure_filename
from nyc_data import NYCPropertyClient
from workbook_io import build_result_zip, read_addresses

BASE=Path(__file__).resolve().parent
MAX_BYTES=int(os.getenv("MAX_UPLOAD_MB","20"))*1024*1024
MAX_ADDRESSES=int(os.getenv("MAX_ADDRESSES","100"))
WORKERS=max(1,min(int(os.getenv("LOOKUP_WORKERS","1")),4))
app=FastAPI(title="NYC Property Batch Lookup",version="3.0.0")
app.mount("/static",StaticFiles(directory=BASE/"static"),name="static")
templates=Jinja2Templates(directory=BASE/"templates")

def _timestamped_zip_name(stem,suffix,now=None):
    current=now or datetime.now()
    stamp=f"{current:%Y%m%d_%H%M%S}_{current.microsecond//1000:03d}"
    return f"{secure_filename(stem) or 'property'}_{suffix}_{stamp}.zip"

def _process(addresses,source,retry_base_url):
    records,documents=[],{}
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for address,(record,files) in zip(addresses,pool.map(lambda value:NYCPropertyClient().lookup(value),addresses)):
            records.append(record)
            if files: documents[address]=files
    return build_result_zip(records,documents,source,retry_base_url=retry_base_url)

@app.get("/",response_class=HTMLResponse)
def index(request:Request):
    return templates.TemplateResponse(request=request,name="index.html",context={})

@app.get("/retry-co")
def retry_co(bin: str, address: str = ""):
    """Retry one CO download synchronously so it remains Cloud Run-safe."""
    if not re.fullmatch(r"\d{7}", bin):
        raise HTTPException(status_code=400, detail="BIN must contain exactly 7 digits.")

    try:
        record, files = NYCPropertyClient().download_co(address, bin)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"CO download failed: {exc}") from exc
    if not files:
        raise HTTPException(status_code=404, detail="No CO document was available for this BIN.")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for filename, content in files:
            archive.writestr(filename, content)

    handle = tempfile.NamedTemporaryFile(prefix="co-retry-", suffix=".zip", delete=False)
    path = Path(handle.name)
    try:
        handle.write(buffer.getvalue())
        handle.close()
    except Exception:
        handle.close()
        path.unlink(missing_ok=True)
        raise
    return FileResponse(
        path,
        filename=f"co_{bin}.zip",
        media_type="application/zip",
        background=BackgroundTask(path.unlink, missing_ok=True),
    )
@app.get("/health")
def health():
    return {"status":"ok","service":"getAddressInfo","runtime":"cloud-run"}

@app.post("/api/process")
async def process_workbook(request:Request,workbook:UploadFile=File(...),column:str=Form("Jobsite")):
    filename=workbook.filename or "input.xlsx"
    if Path(filename).suffix.lower() not in {".xlsx",".xlsm"}:
        raise HTTPException(400,"目前只支持 .xlsx 或 .xlsm 文件")
    content=await workbook.read(MAX_BYTES+1)
    if len(content)>MAX_BYTES: raise HTTPException(413,"上传文件过大")
    try:
        addresses,source=read_addresses(content,column.strip() or "Jobsite")
    except ValueError as exc:
        raise HTTPException(400,str(exc)) from exc
    if not addresses: raise HTTPException(400,"没有找到非空地址")
    if len(addresses)>MAX_ADDRESSES:
        raise HTTPException(400,f"一次最多处理 {MAX_ADDRESSES} 个地址")

    # Cloud Run request-based CPU can pause after a response ends. Keep the
    # complete lookup inside this request instead of using detached threads.
    payload=_process(addresses,source,str(request.base_url))
    handle=tempfile.NamedTemporaryFile(prefix="getaddressinfo-",suffix=".zip",delete=False)
    path=Path(handle.name)
    try:
        handle.write(payload)
        handle.close()
    except Exception:
        handle.close()
        path.unlink(missing_ok=True)
        raise
    return FileResponse(path,filename=_timestamped_zip_name(Path(filename).stem,"results"),media_type="application/zip",background=BackgroundTask(path.unlink,missing_ok=True))
