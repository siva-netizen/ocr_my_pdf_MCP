import logging

import magic
from fastapi import HTTPException

from pipeline import pipeline
from schemas import OCRResponse

logger = logging.getLogger(__name__)

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
SUPPORTED_LABEL = ", ".join(sorted(_FRIENDLY_MIME_NAMES.values()))
FRIENDLY_MIME_NAMES = _FRIENDLY_MIME_NAMES


def detect_mime(data: bytes) -> str:
    return magic.from_buffer(data, mime=True)


def validate(file_bytes: bytes, filename: str = "") -> str:
    if not file_bytes:
        return "The uploaded file is empty. Please upload a valid document."
    if len(file_bytes) > MAX_FILE_SIZE:
        mb = len(file_bytes) / (1024 ** 2)
        return f"File is too large ({mb:.1f} MB). Maximum allowed size is 50 MB."
    detected = detect_mime(file_bytes)
    if detected not in SUPPORTED_MIME_TYPES:
        hint = f'"{filename}"' if filename else "the uploaded file"
        return (
            f"{hint} appears to be a {detected.split('/')[-1].upper()} file, which is not supported. "
            f"Please upload one of the following: {SUPPORTED_LABEL}."
        )
    return ""


def invoke(file_bytes: bytes, mime_type: str, llm_provider: str | None = None,
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
