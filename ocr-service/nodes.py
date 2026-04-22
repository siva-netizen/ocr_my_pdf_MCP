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


def extract_images_node(state: OCRState) -> OCRState:
    """Extract embedded images from OCR'd PDF bytes using PyMuPDF."""
    try:
        import fitz
        doc = fitz.open(stream=state["ocr_output_bytes"], filetype="pdf")
        images = []
        for page_num, page in enumerate(doc, start=1):
            for img in page.get_images(full=True):
                xref = img[0]
                data = doc.extract_image(xref)
                if data["width"] < 100 or data["height"] < 100:
                    continue
                images.append({"page": page_num, "bytes": data["image"], "ext": data["ext"]})
        return {**state, "extracted_images": images, "status": "images_extracted"}
    except Exception as e:
        return {**state, "error": str(e), "status": "failed"}


def caption_images_node(state: OCRState) -> OCRState:
    """Caption each extracted image via Gemini Vision API."""
    import base64
    import os
    import google.generativeai as genai

    if not state["extracted_images"]:
        return {**state, "image_captions": [], "status": "captions_done"}
    try:
        genai.configure(api_key=os.environ["GEMINI_API_KEY"])
        model = genai.GenerativeModel("gemini-2.5-flash")
        captions = []
        for img in state["extracted_images"]:
            response = model.generate_content([
                {"mime_type": f"image/{img['ext']}", "data": base64.b64encode(img["bytes"]).decode()},
                "Describe this image in detail. Focus on any text, diagrams, charts, or visual content relevant to document understanding. Be concise but complete.",
            ])
            captions.append({"page": img["page"], "caption": response.text})
        return {**state, "image_captions": captions, "status": "captions_done"}
    except Exception as e:
        return {**state, "error": str(e), "status": "failed"}


def merge_content_node(state: OCRState) -> OCRState:
    """Merge OCR text and image captions into a unified page-structured string."""
    try:
        import io
        import pdfplumber
        with pdfplumber.open(io.BytesIO(state["ocr_output_bytes"])) as pdf:
            page_texts = [page.extract_text() or "" for page in pdf.pages]

        captions_by_page: dict[int, list[str]] = {}
        for cap in (state["image_captions"] or []):
            captions_by_page.setdefault(cap["page"], []).append(cap["caption"])

        parts = []
        for i, text in enumerate(page_texts, start=1):
            parts.append(f"=== PAGE {i} ===")
            parts.append(f"[TEXT]\n{text}")
            for j, caption in enumerate(captions_by_page.get(i, []), start=1):
                parts.append(f"[IMAGE {j} - Page {i}]\n{caption}")

        return {**state, "merged_content": "\n".join(parts), "status": "merged"}
    except Exception as e:
        return {**state, "error": str(e), "status": "failed"}


def format_response_node(state: OCRState) -> OCRState:
    """Pass through state — all fields already set by upstream nodes."""
    return state
