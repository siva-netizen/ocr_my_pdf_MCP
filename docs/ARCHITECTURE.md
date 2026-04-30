# Architecture & Design — OCR PDF Service

## Overview

A stateless FastAPI service that accepts PDF and image files, runs OCR, extracts and captions embedded images using a Vision Language Model (VLM), and returns structured output as JSON or a downloadable positional Markdown file. The processing pipeline is implemented as a LangGraph state machine, giving each stage a well-defined contract and clean failure routing.

---

## File Structure

```
ocr-service/
├── main.py          # App init + router registration
├── utils.py         # MIME detection, validation, pipeline invocation
├── routers/
│   └── ocr.py       # All route handlers (APIRouter)
├── pipeline.py      # LangGraph graph definition
├── nodes.py         # Pipeline node functions
├── llm_providers.py # CaptionProvider ABC + Gemini/Groq implementations
└── schemas.py       # OCRState, OCRResponse, CaptionItem
```

---

## System Diagram

```
Client (curl / Claude Desktop / REST)
        │
        ▼
┌─────────────────────────────────┐
│         FastAPI Layer           │  routers/ocr.py
│  /ocr                           │  • MIME detection (magic bytes)
│  /ocr/download                  │  • Size + type validation
│  /ocr-base64                    │  • Single x-api-key header
│  /health  /supported-formats    │  • Runs pipeline in threadpool
└────────────┬────────────────────┘
             │ OCRState dict
             ▼
┌─────────────────────────────────────────────────────────┐
│                  LangGraph Pipeline                      │  pipeline.py
│                                                         │
│  convert_to_pdf ──► validate_pdf ──► run_ocr            │
│                                          │              │
│                                    extract_text         │
│                                          │              │
│                                    extract_images       │
│                                          │              │
│                                    caption_images ──► merge_content
│                                                         │
└─────────────────────────────────────────────────────────┘
             │ OCRResponse
             ▼
   JSON / .txt / .md / .json file
```

---

## Pipeline Stages

### 1. `convert_to_pdf_node`

**Purpose:** Normalise all input formats to PDF bytes before any OCR work begins.

| Input MIME | Strategy |
|---|---|
| `application/pdf` | Pass-through — no conversion |
| `image/*` (png, jpeg, tiff, bmp, gif, webp) | `img2pdf.convert()` — lossless wrapping |

> Office formats (`.docx`, `.pptx`) are not supported. LibreOffice was removed to keep the Docker image small.

### 2. `validate_pdf_node`

**Purpose:** Fail fast before invoking the expensive OCR engine.

Checks:
- `pdf_bytes` is non-empty
- Starts with the `%PDF` magic header

If either check fails, `state["error"]` is set and the graph routes to `END` immediately.

### 3. `run_ocr_node`

**Purpose:** Produce a searchable PDF with a clean text layer.

Uses `ocrmypdf` with `force_ocr=True`. This flag discards any existing (potentially broken, encoded, or copy-protected) text layer and replaces it entirely with Tesseract's OCR output. The result is stored in `state["ocr_output_bytes"]`.

`force_ocr=True` is a deliberate design choice: without it, PDFs that already have a text layer (even a garbled one) would skip OCR and return the broken text.

### 4. `extract_text_node`

**Purpose:** Extract plain text per page and detect garbled mathematical notation.

Uses `pdfplumber` to extract text from the OCR'd PDF. Stores:
- `page_texts: list[str]` — one entry per page (reused by `merge_content_node` to avoid re-parsing)
- `extracted_text: str` — full document text joined by newlines
- `page_count: int`
- `garbled_math_pages: list[int] | None` — 1-indexed pages where math garbling is suspected

**Garbled math detection (`_is_garbled_math`)** runs three independent heuristics:

| # | Heuristic | Rationale |
|---|---|---|
| 1 | Symbol density > 35% of characters | Garbled math is punctuation-dense; normal prose is not |
| 2 | Run of 3+ consecutive non-word chars on non-code lines | Tesseract produces runs like `—-(w(` for equations; code lines (`x = arr[i+1]`) are excluded by prefix matching |
| 3 | Apostrophe-paren pattern `[a-z]'\)` | Tesseract's specific failure mode for superscripts: `h_u^(k)` → `h') ` |

Heuristic 2 skips lines matching a code-line prefix regex (`def `, `class `, `import `, `#`, `//`, `var `, `let `, `const `, `word=(`) to avoid false positives on CS textbooks and programming documentation.

### 5. `extract_images_node`

**Purpose:** Extract embedded raster images with their vertical position on the page.

Uses PyMuPDF (`fitz`) to:
1. Build an `xref → y0` map from `page.get_image_info(xrefs=True)` — this gives the bounding box of each image placement on the page.
2. Extract raw image bytes via `doc.extract_image(xref)`.
3. Filter out images smaller than 100×100 px (decorative icons, bullets, borders).

Each image dict carries: `page`, `bytes`, `ext`, `y0`.

The `y0` coordinate enables positional Markdown output — images are sorted into the text flow at their actual vertical position rather than appended at the end of the page.

### 6. `caption_images_node`

**Purpose:** Generate structured ASCII + semantic descriptions for each image using a VLM.

Images are captioned in parallel using `ThreadPoolExecutor(max_workers=5)`. Each image is sent to the configured `CaptionProvider` with a structured prompt that demands three sections:

```
[ASCII]       — ASCII art reproduction of the diagram
[DESCRIPTION] — 2-4 sentence semantic explanation
[CONFIDENCE]  — HIGH / MEDIUM / LOW accuracy rating
```

After receiving the raw caption string, `parse_caption()` extracts the three sections. The full raw caption is preserved alongside the parsed fields so no information is lost if parsing is imperfect.

**`parse_caption()` robustness:**
- Normalises Gemini's markdown header drift (`## ASCII Representation`) to bracket format before parsing
- Extracts `HIGH/MEDIUM/LOW` anywhere in the confidence block — handles prose like `Rate ASCII accuracy: HIGH`
- Returns `ascii=None` (not the string `"NONE"`) when the ASCII block contains only `NONE`
- Defaults `confidence` to `MEDIUM` if the section is missing or unrecognised

### 7. `merge_content_node`

**Purpose:** Produce a single positional Markdown document interleaving text and images in reading order.

For each page:
1. Extract words with `pdfplumber.extract_words()` to get per-word `top` (y) coordinates.
2. Group words into lines by y-proximity (within 3pt tolerance).
3. Collect image caption blocks with their `y0` coordinates.
4. Sort all items (text lines + image blocks) by y-coordinate.
5. Emit in sorted order.

Page headers include an inline warning for garbled math pages:
```markdown
## Page 3  ⚠️ _Math equations on this page may be garbled — manual review recommended_
```

Image blocks with `confidence=LOW` are flagged inline:
```
[IMAGE 1 - Page 3] ⚠️ LOW CONFIDENCE
```

---

## LLM Provider Architecture

```
CaptionProvider (ABC)
    ├── GeminiProvider   — google-genai SDK, gemini-2.5-flash, native image bytes
    └── GroqProvider     — langchain-groq, llama-4-scout-17b-16e-instruct, base64 image_url
```

Both providers share the same `_CAPTION_PROMPT`. Provider selection priority:

1. Per-request `?provider=` query param
2. `LLM_PROVIDER` environment variable
3. Default: `gemini`

**Per-request API keys** — users pass their own key via a single `x-api-key` request header. The server-level env var (`GEMINI_API_KEY` / `GROQ_API_KEY`) is used as a fallback. The server can run with no keys configured at all — captioning is simply skipped and OCR text is still returned.

Provider instances are cached by `(provider_name, api_key)` tuple — same key reuses the same client instance across requests.

---

## Input Validation

All POST endpoints run `_validate()` before entering the pipeline. It checks in order:

1. **Empty file** — returns a friendly message before any processing
2. **File size** — rejects files over 50 MB with the actual size in the message
3. **MIME type** — detected from magic bytes (not filename or `Content-Type` header)

Supported MIME types: `application/pdf`, `image/png`, `image/jpeg`, `image/tiff`, `image/bmp`, `image/gif`, `image/webp`

Example error messages:
```
"The uploaded file is empty. Please upload a valid document."
"File is too large (67.3 MB). Maximum allowed size is 50 MB."
"\"report.xlsx\" appears to be a XLSX file, which is not supported.
 Please upload one of the following: BMP image, GIF image, JPEG image, PDF, PNG image, TIFF image, WebP image."
```

---

## API Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/health` | Liveness check |
| GET | `/supported-formats` | List accepted file types and size limit |
| POST | `/ocr` | OCR a file, returns JSON |
| POST | `/ocr/download` | OCR a file, returns downloadable file |
| POST | `/ocr-base64` | OCR a base64-encoded file, returns JSON |

---

## Data Schemas

### `OCRState` (pipeline internal)

```python
file_bytes: bytes
mime_type: str
llm_provider: str | None         # per-request provider override
api_key: str | None              # per-request key from request header
pdf_bytes: bytes | None
ocr_output_bytes: bytes | None
page_texts: list[str] | None     # per-page text, avoids re-parsing
extracted_text: str | None
extracted_images: list[dict] | None   # {page, bytes, ext, y0}
image_captions: list[dict] | None     # {page, y0, caption, ascii, description, confidence}
merged_content: str | None
page_count: int | None
garbled_math_pages: list[int] | None  # 1-indexed
error: str | None
status: Literal["pending", "ocr_done", "text_extracted",
                "images_extracted", "captions_done", "merged", "failed"]
```

### `OCRResponse` (API output)

```python
text: str
page_count: int
status: str
merged_content: str | None
garbled_math_pages: list[int] | None
image_captions: list[CaptionItem] | None

# CaptionItem:
#   page: int
#   caption: str        # full raw VLM output preserved
#   ascii: str | None   # None if image is decorative
#   description: str
#   confidence: str     # HIGH / MEDIUM / LOW
```

---

## Error Handling & Failure Routing

The LangGraph graph uses conditional edges after every node that can fail. Any node that sets `state["error"]` causes the router to send the graph to `END` immediately — no subsequent nodes run.

- An OCR failure does not attempt text extraction.
- An image extraction failure does not attempt captioning.
- A single image caption failure (network error, API rate limit) is caught per-image; the remaining images are still captioned.

---

## Edge Cases Covered

### Input Validation
| Case | Handling |
|---|---|
| Empty file | Friendly 422 before pipeline entry |
| File > 50 MB | Friendly 422 with actual size shown |
| MIME type spoofing (wrong extension or Content-Type) | MIME detected from magic bytes — extension ignored |
| Unsupported file type | Friendly 422 listing all supported formats by name |
| Invalid base64 in `/ocr-base64` | Friendly 422 with re-encode suggestion |

### PDF Processing
| Case | Handling |
|---|---|
| PDF with existing broken/encoded text layer | `force_ocr=True` discards it and re-OCRs from scratch |
| PDF with valid `%PDF` header but corrupt body | `ocrmypdf` raises, caught and returned as 500 |
| Non-PDF bytes with `.pdf` extension | `validate_pdf_node` checks magic header, fails fast |
| Empty PDF (0 bytes) | `validate_pdf_node` catches before OCR |

### Image Extraction & Captioning
| Case | Handling |
|---|---|
| Tiny decorative images (icons, bullets) | Filtered out at < 100×100 px |
| Image with no placement bbox in PDF | `y0` defaults to `0.0` (top of page) |
| VLM API failure for one image | Per-image try/except; other images still captioned |
| No API key (header or env) | `get_provider()` returns `None`; captioning skipped, OCR text still returned |
| VLM uses markdown headers instead of bracket format | `parse_caption()` normalises `## ASCII Representation` → `[ASCII]` before parsing |
| VLM returns prose in confidence block | `\b(HIGH\|MEDIUM\|LOW)\b` scan finds the keyword anywhere in the block |
| VLM returns `NONE` in ASCII block | `ascii` field set to `None`, not the string `"NONE"` |
| VLM adds chain-of-thought preamble | Prompt uses `CRITICAL: Output ONLY...` to suppress it |

### Math & Text Quality
| Case | Handling |
|---|---|
| Garbled mathematical notation from Tesseract | Three-heuristic detector flags affected pages in output and API response |
| Code blocks triggering garbled-math false positives | Heuristic 2 skips lines matching code-line prefix patterns |
| Page with no extractable text | `pdfplumber` returns `""`, stored as empty string, no crash |

### Concurrency & Resources
| Case | Handling |
|---|---|
| Blocking OCR on async endpoint | `run_in_threadpool` prevents event loop blocking |
| Multiple simultaneous caption API calls | `ThreadPoolExecutor(max_workers=5)` caps concurrency |
| Same API key reused across requests | Singleton cache keyed by `(provider, api_key)` — one client instance per key |

---

## Known Limitations & Future Work

| Limitation | Notes |
|---|---|
| Office files not supported | LibreOffice removed to reduce image size. Future: separate conversion sidecar |
| Garbled math in text layer | Heuristic detection only; no correction. Future: MathPix API or dedicated math OCR |
| No persistent storage | Results returned in-memory; no caching or job queue |
| Image position uses y0 only | Multi-column layouts may interleave incorrectly. Future: x0-aware column sorting |
| No authentication | API keys are the user's own. Add a gateway/auth layer for production use |
