import io
import ocrmypdf
import pdfplumber
from schemas import OCRState


def validate_pdf_node(state: OCRState) -> OCRState:
    """Validate that pdf_bytes is non-empty and has a valid PDF header."""
    if not state["pdf_bytes"]:
        return {**state, "error": "Empty file", "status": "failed"}
    if not state["pdf_bytes"].startswith(b"%PDF"):
        return {**state, "error": "Not a valid PDF", "status": "failed"}
    return state


def run_ocr_node(state: OCRState) -> OCRState:
    """Run OCR on pdf_bytes using ocrmypdf and return ocr_output_bytes."""
    input_buf = io.BytesIO(state["pdf_bytes"])
    output_buf = io.BytesIO()
    try:
        ocrmypdf.ocr(input_buf, output_buf, skip_text=True)
        return {**state, "ocr_output_bytes": output_buf.getvalue(), "status": "ocr_done"}
    except Exception as e:
        return {**state, "error": str(e), "status": "failed"}


def extract_text_node(state: OCRState) -> OCRState:
    """Extract plain text and page count from ocr_output_bytes using pdfplumber."""
    with pdfplumber.open(io.BytesIO(state["ocr_output_bytes"])) as pdf:
        text = "\n".join(page.extract_text() or "" for page in pdf.pages)
        page_count = len(pdf.pages)
    return {**state, "extracted_text": text, "page_count": page_count, "status": "text_extracted"}


def format_response_node(state: OCRState) -> OCRState:
    """Pass through state — page_count and text already set by extract_text_node."""
    return state
