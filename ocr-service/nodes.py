import io
import logging
import os
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor

import fitz
import img2pdf
import ocrmypdf
import pdfplumber

from llm_providers import get_provider
from schemas import OCRState

logger = logging.getLogger(__name__)


def convert_to_pdf_node(state: OCRState) -> OCRState:
    """Convert DOCX/PPTX/images to PDF bytes. Pass through if already PDF."""
    mime = state["mime_type"]
    data = state["file_bytes"]

    if mime == "application/pdf":
        return {**state, "pdf_bytes": data}

    if mime in ("image/png", "image/jpeg", "image/jpg", "image/tiff", "image/bmp", "image/gif", "image/webp"):
        try:
            return {**state, "pdf_bytes": img2pdf.convert(data)}
        except Exception as e:
            logger.error("Image to PDF conversion failed: %s", e)
            return {**state, "error": "Image conversion failed", "status": "failed"}

    office_exts = {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
        "application/msword": ".doc",
        "application/vnd.ms-powerpoint": ".ppt",
    }
    if mime in office_exts:
        ext = office_exts[mime]
        with tempfile.TemporaryDirectory() as tmpdir:
            infile = os.path.join(tmpdir, f"input{ext}")
            with open(infile, "wb") as f:
                f.write(data)
            # Fix #11 — catch TimeoutExpired
            try:
                result = subprocess.run(
                    ["libreoffice", "--headless", "--convert-to", "pdf", "--outdir", tmpdir, infile],
                    capture_output=True, timeout=120,
                )
            except subprocess.TimeoutExpired:
                logger.error("LibreOffice conversion timed out for mime=%s", mime)
                return {**state, "error": "Document conversion timed out", "status": "failed"}
            # Fix #5 — log stderr internally, don't expose to caller
            if result.returncode != 0:
                logger.error("LibreOffice conversion failed: %s", result.stderr.decode())
                return {**state, "error": "Document conversion failed", "status": "failed"}
            pdf_path = infile.replace(ext, ".pdf")
            with open(pdf_path, "rb") as f:
                return {**state, "pdf_bytes": f.read()}

    return {**state, "error": f"Unsupported file type: {mime}", "status": "failed"}


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
        # force_ocr=True discards the original (broken/encoded) text layer
        # and replaces it entirely with Tesseract's OCR output
        ocrmypdf.ocr(input_buf, output_buf, force_ocr=True)
        return {**state, "ocr_output_bytes": output_buf.getvalue(), "status": "ocr_done"}
    except Exception as e:
        logger.error("OCR failed: %s", e)
        return {**state, "error": str(e), "status": "failed"}


def extract_text_node(state: OCRState) -> OCRState:
    """Extract plain text and page count from ocr_output_bytes using pdfplumber."""
    with pdfplumber.open(io.BytesIO(state["ocr_output_bytes"])) as pdf:
        # Fix #6 — store page_texts list to avoid re-parsing in merge_content_node
        page_texts = [page.extract_text() or "" for page in pdf.pages]
        page_count = len(pdf.pages)
    return {
        **state,
        "page_texts": page_texts,
        "extracted_text": "\n".join(page_texts),
        "page_count": page_count,
        "status": "text_extracted",
    }


def extract_images_node(state: OCRState) -> OCRState:
    """Extract embedded images from OCR'd PDF bytes using PyMuPDF."""
    try:
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
        logger.error("Image extraction failed: %s", e)
        return {**state, "error": str(e), "status": "failed"}


def caption_images_node(state: OCRState) -> OCRState:
    """Caption each extracted image using the configured LLM provider."""
    if not state["extracted_images"]:
        return {**state, "image_captions": [], "status": "captions_done"}

    provider = get_provider(state.get("llm_provider"))
    if provider is None:
        logger.warning("No LLM provider available; skipping image captioning")
        return {**state, "image_captions": [], "status": "captions_done"}

    def _caption_one(img: dict) -> dict | None:
        try:
            return {"page": img["page"], "caption": provider.caption(img["bytes"], img["ext"])}
        except Exception as e:
            logger.error("Caption failed for page %d: %s", img["page"], e)
            return None

    with ThreadPoolExecutor(max_workers=5) as ex:
        results = list(ex.map(_caption_one, state["extracted_images"]))

    captions = [r for r in results if r is not None]
    logger.info("Captioned %d/%d images", len(captions), len(state["extracted_images"]))
    return {**state, "image_captions": captions, "status": "captions_done"}


def merge_content_node(state: OCRState) -> OCRState:
    """Merge OCR text and image captions into a unified page-structured string."""
    try:
        # Fix #6 — use pre-computed page_texts, no re-parse
        page_texts = state["page_texts"] or []
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
        logger.error("Merge failed: %s", e)
        return {**state, "error": str(e), "status": "failed"}
