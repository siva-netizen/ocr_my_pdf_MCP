from typing import Literal
from typing_extensions import TypedDict
from pydantic import BaseModel


class OCRState(TypedDict):
    file_bytes: bytes
    mime_type: str
    llm_provider: str | None
    api_key: str | None          # per-request API key from request header
    pdf_bytes: bytes | None
    ocr_output_bytes: bytes | None
    page_texts: list[str] | None
    extracted_text: str | None
    extracted_images: list[dict] | None
    image_captions: list[dict] | None
    merged_content: str | None
    page_count: int | None
    garbled_math_pages: list[int] | None  # 1-indexed pages with suspected garbled equations
    error: str | None
    status: Literal["pending", "ocr_done", "text_extracted", "images_extracted", "captions_done", "merged", "failed"]


class CaptionItem(BaseModel):
    page: int
    caption: str          # full raw caption preserved
    ascii: str | None = None
    description: str = ""
    confidence: str = "MEDIUM"  # HIGH / MEDIUM / LOW


class OCRResponse(BaseModel):
    text: str
    page_count: int
    status: str
    image_captions: list[CaptionItem] | None = None
    merged_content: str | None = None
    garbled_math_pages: list[int] | None = None  # pages flagged for manual math review
