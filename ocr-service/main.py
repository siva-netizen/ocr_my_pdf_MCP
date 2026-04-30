import base64
import logging

import magic
from fastapi import FastAPI, UploadFile, HTTPException, Header
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import PlainTextResponse

from pipeline import pipeline
from schemas import OCRResponse

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI()

MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB

SUPPORTED_MIME_TYPES = {
    "application/pdf",
    "image/png", "image/jpeg", "image/tiff", "image/bmp", "image/gif", "image/webp",
}

_FRIENDLY_MIME_NAMES = {
    "application/pdf": "PDF",
    "image/png": "PNG image", "image/jpeg": "JPEG image",
    "image/tiff": "TIFF image", "image/bmp": "BMP image",
    "image/gif": "GIF image", "image/webp": "WebP image",
}
_SUPPORTED_LABEL = ", ".join(sorted(_FRIENDLY_MIME_NAMES.values()))


def _detect_mime(data: bytes) -> str:
    return magic.from_buffer(data, mime=True)


def _validate(file_bytes: bytes, filename: str = "") -> str:
    """Detect MIME and return a user-friendly error string, or empty string if valid."""
    if not file_bytes:
        return "The uploaded file is empty. Please upload a valid document."
    if len(file_bytes) > MAX_FILE_SIZE:
        mb = len(file_bytes) / (1024 ** 2)
        return f"File is too large ({mb:.1f} MB). Maximum allowed size is 50 MB."
    detected = _detect_mime(file_bytes)
    if detected not in SUPPORTED_MIME_TYPES:
        hint = f'"{filename}"' if filename else "the uploaded file"
        return (
            f"{hint} appears to be a {detected.split('/')[-1].upper()} file, which is not supported. "
            f"Please upload one of the following: {_SUPPORTED_LABEL}."
        )
    return ""


def _resolve_api_key(provider: str | None, gemini_key: str | None, groq_key: str | None) -> str | None:
    return groq_key if (provider or "gemini").lower() == "groq" else gemini_key


def _invoke(file_bytes: bytes, mime_type: str, llm_provider: str | None = None,
            api_key: str | None = None) -> OCRResponse:
    result = pipeline.invoke({
        "file_bytes": file_bytes,
        "mime_type": mime_type,
        "llm_provider": llm_provider,
        "api_key": api_key,
        "pdf_bytes": None,
        "ocr_output_bytes": None,
        "page_texts": None,
        "extracted_text": None,
        "extracted_images": None,
        "image_captions": None,
        "merged_content": None,
        "page_count": None,
        "garbled_math_pages": None,
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
        garbled_math_pages=result.get("garbled_math_pages"),
    )


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/supported-formats")
def supported_formats():
    """List the file types accepted by this service."""
    return {
        "supported": sorted(_FRIENDLY_MIME_NAMES.values()),
        "max_file_size_mb": MAX_FILE_SIZE // (1024 * 1024),
    }


@app.post("/ocr", response_model=OCRResponse)
async def ocr(
    file: UploadFile,
    provider: str | None = None,
    x_gemini_api_key: str | None = Header(default=None),
    x_groq_api_key: str | None = Header(default=None),
):
    file_bytes = await file.read()
    if err := _validate(file_bytes, file.filename or ""):
        raise HTTPException(status_code=422, detail=err)
    detected = _detect_mime(file_bytes)
    api_key = _resolve_api_key(provider, x_gemini_api_key, x_groq_api_key)
    logger.info("OCR request: detected_mime=%s size=%d provider=%s", detected, len(file_bytes), provider)
    return await run_in_threadpool(_invoke, file_bytes, detected, provider, api_key)


@app.post("/ocr/download")
async def ocr_download(
    file: UploadFile,
    provider: str | None = None,
    format: str = "txt",
    x_gemini_api_key: str | None = Header(default=None),
    x_groq_api_key: str | None = Header(default=None),
):
    file_bytes = await file.read()
    if err := _validate(file_bytes, file.filename or ""):
        raise HTTPException(status_code=422, detail=err)
    detected = _detect_mime(file_bytes)
    api_key = _resolve_api_key(provider, x_gemini_api_key, x_groq_api_key)
    result = await run_in_threadpool(_invoke, file_bytes, detected, provider, api_key)
    stem = file.filename.rsplit(".", 1)[0] if file.filename else "output"

    if format == "json":
        import json
        from fastapi.responses import Response
        content = json.dumps(result.model_dump(), indent=2)
        return Response(content, media_type="application/json",
                        headers={"Content-Disposition": f'attachment; filename="{stem}.json"'})
    if format == "md":
        content = result.merged_content or result.text or ""
        return PlainTextResponse(content, media_type="text/markdown",
                                 headers={"Content-Disposition": f'attachment; filename="{stem}.md"'})
    content = result.merged_content or result.text or ""
    return PlainTextResponse(content, headers={"Content-Disposition": f'attachment; filename="{stem}.txt"'})


@app.post("/ocr-base64", response_model=OCRResponse)
async def ocr_base64(
    payload: dict,
    provider: str | None = None,
    x_gemini_api_key: str | None = Header(default=None),
    x_groq_api_key: str | None = Header(default=None),
):
    raw = payload.get("file_base64") or payload.get("pdf_base64")
    if not raw:
        raise HTTPException(status_code=422, detail="Missing 'file_base64' field in request body.")
    try:
        file_bytes = base64.b64decode(raw)
    except Exception:
        raise HTTPException(status_code=422, detail="Invalid base64 encoding. Please re-encode your file and try again.")
    if err := _validate(file_bytes):
        raise HTTPException(status_code=422, detail=err)
    detected = _detect_mime(file_bytes)
    api_key = _resolve_api_key(provider, x_gemini_api_key, x_groq_api_key)
    logger.info("OCR-base64 request: detected_mime=%s size=%d provider=%s", detected, len(file_bytes), provider)
    return await run_in_threadpool(_invoke, file_bytes, detected, provider, api_key)
