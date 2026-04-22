from typing import Literal
from typing_extensions import TypedDict
from pydantic import BaseModel


class OCRState(TypedDict):
    pdf_bytes: bytes
    ocr_output_bytes: bytes | None
    extracted_text: str | None
    page_count: int | None
    error: str | None
    status: Literal["pending", "ocr_done", "text_extracted", "failed"]


class OCRResponse(BaseModel):
    text: str
    page_count: int
    status: str
