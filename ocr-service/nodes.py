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


def _is_garbled_math(text: str) -> bool:
    """Heuristic: detect OCR-garbled mathematical notation on a page.

    Signals:
    - High density of non-ASCII / replacement characters (OCR confusion)
    - Runs of punctuation/symbols not forming valid words
    - Subscript/superscript Unicode that Tesseract mangles into random chars
    """
    if not text:
        return False
    # Ratio of non-alphanumeric, non-space chars — garbled math is symbol-dense
    non_word = sum(1 for c in text if not c.isalnum() and not c.isspace())
    if len(text) > 0 and non_word / len(text) > 0.35:
        return True
    # Runs of 3+ consecutive punctuation/symbol chars, skipping code-like lines
    _CODE_LINE = re.compile(r"^\s*(def |class |import |from |#|//|var |let |const |\w+\s*[=({])")
    for line in text.splitlines():
        if _CODE_LINE.match(line):
            continue
        if re.search(r"[^\w\s]{3,}", line):
            return True
    # Common Tesseract math-garble patterns
    if re.search(r"[a-z]\s*['\u2019\u02bc]\s*\)", text):  # h') or h')
        return True
    return False


def extract_text_node(state: OCRState) -> OCRState:
    """Extract plain text and page count; flag pages with suspected garbled math."""
    with pdfplumber.open(io.BytesIO(state["ocr_output_bytes"])) as pdf:
        page_texts = [page.extract_text() or "" for page in pdf.pages]
        page_count = len(pdf.pages)

    garbled = [i + 1 for i, t in enumerate(page_texts) if _is_garbled_math(t)]
    if garbled:
        logger.warning("Suspected garbled math on pages: %s", garbled)

    return {
        **state,
        "page_texts": page_texts,
        "extracted_text": "\n".join(page_texts),
        "page_count": page_count,
        "garbled_math_pages": garbled or None,
        "status": "text_extracted",
    }


def extract_images_node(state: OCRState) -> OCRState:
    """Extract embedded images from OCR'd PDF bytes using PyMuPDF, recording bbox y0."""
    try:
        doc = fitz.open(stream=state["ocr_output_bytes"], filetype="pdf")
        images = []
        for page_num, page in enumerate(doc, start=1):
            # Build xref -> bbox map from image placements on the page
            xref_to_bbox: dict[int, float] = {}
            for item in page.get_image_info(xrefs=True):
                xref_to_bbox[item["xref"]] = item["bbox"][1]  # y0

            for img in page.get_images(full=True):
                xref = img[0]
                data = doc.extract_image(xref)
                if data["width"] < 100 or data["height"] < 100:
                    continue
                images.append({
                    "page": page_num,
                    "bytes": data["image"],
                    "ext": data["ext"],
                    "y0": xref_to_bbox.get(xref, 0.0),
                })
        return {**state, "extracted_images": images, "status": "images_extracted"}
    except Exception as e:
        logger.error("Image extraction failed: %s", e)
        return {**state, "error": str(e), "status": "failed"}


import re


def parse_caption(raw: str) -> dict:
    """Parse structured caption into ascii, description, confidence fields.

    Handles both [TAG] bracket format and ## TAG markdown header format,
    and confidence values embedded in prose (e.g. 'Rate ASCII accuracy: HIGH').
    """
    # Normalise ## ASCII Representation / ## Description / ## Confidence → [TAG]
    normalised = re.sub(
        r"^#{1,3}\s*(ASCII(?:\s+Representation)?|DESCRIPTION|CONFIDENCE[^\n]*)\s*$",
        lambda m: f"[{m.group(1).split()[0].upper()}]",
        raw, flags=re.M | re.I,
    )

    def _extract(tag: str) -> str:
        m = re.search(
            rf"\[{tag}\]\s*(.*?)(?=\[(?:ASCII|DESCRIPTION|CONFIDENCE)|$)",
            normalised, re.S | re.I,
        )
        return m.group(1).strip() if m else ""

    ascii_block = _extract("ASCII")
    ascii_val = None if not ascii_block or re.fullmatch(r"NONE", ascii_block.strip(), re.I) else ascii_block

    description = _extract("DESCRIPTION")

    # Extract HIGH/MEDIUM/LOW anywhere in the confidence block (handles prose like "Rate ... HIGH")
    conf_block = _extract("CONFIDENCE") or raw
    conf_m = re.search(r"\b(HIGH|MEDIUM|LOW)\b", conf_block, re.I)
    confidence = conf_m.group(1).upper() if conf_m else "MEDIUM"

    return {"ascii": ascii_val, "description": description, "confidence": confidence}


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
            raw = provider.caption(img["bytes"], img["ext"])
            return {"page": img["page"], "y0": img.get("y0", 0.0), "caption": raw, **parse_caption(raw)}
        except Exception as e:
            logger.error("Caption failed for page %d: %s", img["page"], e)
            return None

    with ThreadPoolExecutor(max_workers=5) as ex:
        results = list(ex.map(_caption_one, state["extracted_images"]))

    captions = [r for r in results if r is not None]
    logger.info("Captioned %d/%d images", len(captions), len(state["extracted_images"]))
    return {**state, "image_captions": captions, "status": "captions_done"}


def merge_content_node(state: OCRState) -> OCRState:
    """Merge OCR text and image captions into positional Markdown.

    Text blocks and image captions are sorted by their y0 position on each page,
    so images appear at their origin position in the document flow.
    """
    try:
        import pdfplumber

        doc_bytes = state["ocr_output_bytes"]
        captions_by_page: dict[int, list[dict]] = {}
        for cap in (state["image_captions"] or []):
            captions_by_page.setdefault(cap["page"], []).append(cap)

        garbled_pages = set(state.get("garbled_math_pages") or [])
        parts = []
        with pdfplumber.open(io.BytesIO(doc_bytes)) as pdf:
            for page_num, page in enumerate(pdf.pages, start=1):
                header = f"## Page {page_num}"
                if page_num in garbled_pages:
                    header += "  ⚠️ _Math equations on this page may be garbled — manual review recommended_"
                parts.append(header + "\n")

                # Collect text blocks with y position
                items: list[tuple[float, str]] = []
                words = page.extract_words()
                if words:
                    # Group words into lines by top-y proximity (within 3pt)
                    lines: list[tuple[float, list[str]]] = []
                    for w in words:
                        y = w["top"]
                        if lines and abs(y - lines[-1][0]) < 3:
                            lines[-1][1].append(w["text"])
                        else:
                            lines.append((y, [w["text"]]))
                    for y, tokens in lines:
                        items.append((y, " ".join(tokens)))

                # Collect image captions with y position
                for idx, cap in enumerate(captions_by_page.get(page_num, []), start=1):
                    confidence = cap.get("confidence", "MEDIUM")
                    low_flag = " ⚠️ LOW CONFIDENCE" if confidence == "LOW" else ""
                    block = f"[IMAGE {idx} - Page {page_num}]{low_flag}\n{cap['caption']}"
                    items.append((cap.get("y0", 0.0), block))

                # Sort everything by vertical position
                items.sort(key=lambda x: x[0])
                parts.extend(text for _, text in items)
                parts.append("")  # blank line between pages

        return {**state, "merged_content": "\n".join(parts), "status": "merged"}
    except Exception as e:
        logger.error("Merge failed: %s", e)
        return {**state, "error": str(e), "status": "failed"}
