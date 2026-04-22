from typing import Literal
from typing_extensions import TypedDict
from pydantic import BaseModel


class OCRState(TypedDict):
    pdf_bytes: bytes
    ocr_output_bytes: bytes | None
    extracted_text: str | None
    extracted_images: list[dict] | None
    image_captions: list[dict] | None
    merged_content: str | None
    page_count: int | None
    error: str | None
    status: Literal["pending", "ocr_done", "text_extracted", "images_extracted", "captions_done", "merged", "failed"]


class OCRResponse(BaseModel):
    text: str
    page_count: int
    status: str
    image_captions: list[dict] | None = None
    merged_content: str | None = None
