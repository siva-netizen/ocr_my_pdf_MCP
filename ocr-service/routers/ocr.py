import base64
import json
import logging

from fastapi import APIRouter, Header, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import PlainTextResponse, Response

from schemas import OCRResponse
from utils import FRIENDLY_MIME_NAMES, MAX_FILE_SIZE, detect_mime, invoke, validate

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/health")
def health():
    return {"status": "ok"}


@router.get("/supported-formats")
def supported_formats():
    return {
        "supported": sorted(FRIENDLY_MIME_NAMES.values()),
        "max_file_size_mb": MAX_FILE_SIZE // (1024 * 1024),
    }


@router.post("/ocr", response_model=OCRResponse)
async def ocr(
    file: UploadFile,
    provider: str | None = None,
    x_api_key: str | None = Header(default=None),
):
    file_bytes = await file.read()
    if err := validate(file_bytes, file.filename or ""):
        raise HTTPException(status_code=422, detail=err)
    detected = detect_mime(file_bytes)
    logger.info("OCR request: detected_mime=%s size=%d provider=%s", detected, len(file_bytes), provider)
    return await run_in_threadpool(invoke, file_bytes, detected, provider, x_api_key)


@router.post("/ocr/download")
async def ocr_download(
    file: UploadFile,
    provider: str | None = None,
    format: str = "txt",
    x_api_key: str | None = Header(default=None),
):
    file_bytes = await file.read()
    if err := validate(file_bytes, file.filename or ""):
        raise HTTPException(status_code=422, detail=err)
    detected = detect_mime(file_bytes)
    result = await run_in_threadpool(invoke, file_bytes, detected, provider, x_api_key)
    stem = file.filename.rsplit(".", 1)[0] if file.filename else "output"

    if format == "json":
        content = json.dumps(result.model_dump(), indent=2)
        return Response(content, media_type="application/json",
                        headers={"Content-Disposition": f'attachment; filename="{stem}.json"'})
    content = result.merged_content or result.text or ""
    media_type = "text/markdown" if format == "md" else "text/plain"
    return PlainTextResponse(content, media_type=media_type,
                             headers={"Content-Disposition": f'attachment; filename="{stem}.{format}"'})


@router.post("/ocr-base64", response_model=OCRResponse)
async def ocr_base64(
    payload: dict,
    provider: str | None = None,
    x_api_key: str | None = Header(default=None),
):
    raw = payload.get("file_base64") or payload.get("pdf_base64")
    if not raw:
        raise HTTPException(status_code=422, detail="Missing 'file_base64' field in request body.")
    try:
        file_bytes = base64.b64decode(raw)
    except Exception:
        raise HTTPException(status_code=422, detail="Invalid base64 encoding. Please re-encode your file and try again.")
    if err := validate(file_bytes):
        raise HTTPException(status_code=422, detail=err)
    detected = detect_mime(file_bytes)
    logger.info("OCR-base64 request: detected_mime=%s size=%d provider=%s", detected, len(file_bytes), provider)
    return await run_in_threadpool(invoke, file_bytes, detected, provider, x_api_key)
