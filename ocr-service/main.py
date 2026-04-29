import base64
import logging

import magic
from fastapi import FastAPI, UploadFile, HTTPException
from fastapi.concurrency import run_in_threadpool

from pipeline import pipeline
from schemas import OCRResponse

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI()

MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB

SUPPORTED_MIME_TYPES = {
    "application/pdf",
    "image/png", "image/jpeg", "image/tiff", "image/bmp", "image/gif", "image/webp",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/msword",
    "application/vnd.ms-powerpoint",
}


def _detect_mime(data: bytes) -> str:
    # Fix #4 — detect MIME from magic bytes, not client header
    return magic.from_buffer(data, mime=True)


def _invoke(file_bytes: bytes, mime_type: str, llm_provider: str | None = None) -> OCRResponse:
    result = pipeline.invoke({
        "file_bytes": file_bytes,
        "mime_type": mime_type,
        "llm_provider": llm_provider,
        "pdf_bytes": None,
        "ocr_output_bytes": None,
        "page_texts": None,
        "extracted_text": None,
        "extracted_images": None,
        "image_captions": None,
        "merged_content": None,
        "page_count": None,
        "error": None,
        "status": "pending",
    })
    if result.get("error"):
        logger.error("Pipeline error for mime=%s: %s", mime_type, result["error"])
        raise HTTPException(status_code=500, detail=result["error"])
    return OCRResponse(
        text=result["extracted_text"],
        page_count=result["page_count"],
        status=result["status"],
        image_captions=result.get("image_captions"),
        merged_content=result.get("merged_content"),
    )


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/ocr", response_model=OCRResponse)
async def ocr(file: UploadFile, provider: str | None = None):
    file_bytes = await file.read()

    if len(file_bytes) > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="File too large. Max 50MB.")
    if not file_bytes:
        raise HTTPException(status_code=400, detail="Empty file")

    detected = _detect_mime(file_bytes)
    if detected not in SUPPORTED_MIME_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported file type: {detected}. Supported: {', '.join(sorted(SUPPORTED_MIME_TYPES))}"
        )

    logger.info("OCR request: detected_mime=%s size=%d provider=%s", detected, len(file_bytes), provider)
    return await run_in_threadpool(_invoke, file_bytes, detected, provider)


@app.post("/ocr-base64", response_model=OCRResponse)
async def ocr_base64(payload: dict, provider: str | None = None):
    raw = payload.get("file_base64") or payload.get("pdf_base64")
    if not raw:
        raise HTTPException(status_code=422, detail="Missing field: file_base64")
    try:
        file_bytes = base64.b64decode(raw)
    except Exception:
        raise HTTPException(status_code=422, detail="Invalid base64")

    if len(file_bytes) > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="File too large. Max 50MB.")

    detected = _detect_mime(file_bytes)
    if detected not in SUPPORTED_MIME_TYPES:
        raise HTTPException(status_code=422, detail=f"Unsupported file type: {detected}")

    logger.info("OCR-base64 request: detected_mime=%s size=%d provider=%s", detected, len(file_bytes), provider)
    return await run_in_threadpool(_invoke, file_bytes, detected, provider)
