# Architecture & Design — OCR PDF Service

## Overview

A stateless FastAPI service that accepts documents (PDF, images, Office files), runs OCR, extracts and captions embedded images using a Vision Language Model, and returns structured output as JSON or a downloadable positional Markdown file. The processing pipeline is implemented as a LangGraph state machine, giving each stage a well-defined contract and clean failure routing.

---

## System Diagram

```
Client (curl / Claude Desktop / REST)
        │
        ▼
┌───────────────────┐
│   FastAPI Layer   │  main.py
│  /ocr             │  • MIME detection (magic bytes)
│  /ocr/download    │  • Size guard (50 MB)
│  /ocr-base64      │  • Runs pipeline in threadpool
└────────┬──────────┘
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
| `application/vnd.openxmlformats-officedocument.*`, `application/msword`, `application/vnd.ms-powerpoint` | `libreoffice --headless --convert-to pdf` in a temp directory |

The node writes the converted PDF into `state["pdf_bytes"]` and returns. All downstream nodes operate only on PDF bytes, so the rest of the pipeline is format-agnostic.

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
- `page_texts: list[str]` — one entry per page (used by `merge_content_node` to avoid re-parsing)
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

The `y0` coordinate is the key that enables positional Markdown output — it allows images to be sorted into the text flow at their actual vertical position rather than appended at the end of the page.

### 6. `caption_images_node`

**Purpose:** Generate structured ASCII + semantic descriptions for each image using a VLM.

Images are captioned in parallel using `ThreadPoolExecutor(max_workers=5)`. Each image is sent to the configured `CaptionProvider` with a structured prompt that demands three sections:

```
[ASCII]    — ASCII art reproduction of the diagram
[DESCRIPTION] — 2-4 sentence semantic explanation
[CONFIDENCE]  — HIGH / MEDIUM / LOW accuracy rating
```

After receiving the raw caption string, `parse_caption()` extracts the three sections using regex. The full raw caption is preserved alongside the parsed fields so no information is lost if parsing is imperfect.

**`parse_caption()` robustness:**
- Handles both `[CONFIDENCE]\nHIGH` and `[CONFIDENCE: HIGH]` inline formats
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

Image blocks with `confidence=LOW` are flagged:
```
[IMAGE 1 - Page 3] ⚠️ LOW CONFIDENCE
[ASCII]
...
[DESCRIPTION]
...
[CONFIDENCE: LOW]
```

---

## LLM Provider Architecture

```
CaptionProvider (ABC)
    ├── GeminiProvider   — google-genai SDK, gemini-2.5-flash, native image bytes
    └── GroqProvider     — langchain-groq, llama-4-scout-17b-16e-instruct, base64 image_url
```

Both providers share the same `_CAPTION_PROMPT`. Provider selection:

1. Per-request `?provider=` query param
2. `LLM_PROVIDER` environment variable
3. Default: `gemini`

Providers are instantiated lazily and cached as singletons in `_instances` for the process lifetime. If a provider fails to initialise (missing API key), `get_provider()` returns `None` and captioning is skipped gracefully — OCR text is still returned.

---

## API Endpoints

### `POST /ocr`
Accepts `multipart/form-data` with a `file` field. Returns `OCRResponse` JSON.

### `POST /ocr/download`
Same processing as `/ocr`. Returns a downloadable file.

| `?format=` | Content-Type | Filename |
|---|---|---|
| `txt` (default) | `text/plain` | `{stem}.txt` |
| `md` | `text/markdown` | `{stem}.md` |
| `json` | `application/json` | `{stem}.json` |

### `POST /ocr-base64`
Accepts JSON body with `file_base64` or `pdf_base64` field (base64-encoded file). Useful for MCP tool integration where multipart is inconvenient.

### `GET /health`
Returns `{"status": "ok"}`. Used by Render and Docker health checks.

---

## Data Schemas

### `OCRState` (pipeline internal)

```python
file_bytes: bytes           # raw upload
mime_type: str              # detected MIME
llm_provider: str | None    # per-request provider override
pdf_bytes: bytes | None     # after convert_to_pdf_node
ocr_output_bytes: bytes | None  # after run_ocr_node
page_texts: list[str] | None    # per-page text, avoids re-parsing
extracted_text: str | None      # full joined text
extracted_images: list[dict] | None  # {page, bytes, ext, y0}
image_captions: list[dict] | None    # {page, y0, caption, ascii, description, confidence}
merged_content: str | None      # final positional Markdown
page_count: int | None
garbled_math_pages: list[int] | None  # 1-indexed
error: str | None
status: Literal["pending", "ocr_done", "text_extracted",
                "images_extracted", "captions_done", "merged", "failed"]
```

### `OCRResponse` (API output)

```python
text: str                          # full extracted text
page_count: int
status: str
merged_content: str | None         # positional Markdown
garbled_math_pages: list[int] | None
image_captions: list[CaptionItem] | None

# CaptionItem:
#   page: int
#   caption: str        # full raw VLM output
#   ascii: str | None   # None if image is decorative
#   description: str
#   confidence: str     # HIGH / MEDIUM / LOW
```

---

## Error Handling & Failure Routing

The LangGraph graph uses conditional edges after every node that can fail. Any node that encounters an error sets `state["error"]` and `state["status"] = "failed"`, and the router immediately sends the graph to `END`. This means:

- A LibreOffice conversion failure does not attempt OCR.
- An OCR failure does not attempt text extraction.
- An image extraction failure does not attempt captioning.
- A single image caption failure (network error, API rate limit) is caught per-image and logged; the remaining images are still captioned.

Errors from LibreOffice's stderr are logged internally and never surfaced to the caller (prevents internal path/version leakage).

---

## Edge Cases Covered

### Input Validation
| Case | Handling |
|---|---|
| Empty file upload | 400 before pipeline entry |
| File > 50 MB | 413 before pipeline entry |
| MIME type spoofing (client sends wrong Content-Type) | MIME detected from magic bytes, not HTTP header |
| Unsupported file type | 422 with list of supported types |
| Invalid base64 in `/ocr-base64` | 422 with clear message |

### PDF Processing
| Case | Handling |
|---|---|
| PDF with existing (broken/encoded) text layer | `force_ocr=True` discards it and re-OCRs from scratch |
| PDF with valid `%PDF` header but corrupt body | `ocrmypdf` raises, caught and returned as 500 |
| Non-PDF bytes with `.pdf` extension | `validate_pdf_node` checks magic header, fails fast |
| Empty PDF (0 bytes) | `validate_pdf_node` catches before OCR |

### Office / Image Conversion
| Case | Handling |
|---|---|
| LibreOffice conversion timeout (> 120s) | `subprocess.TimeoutExpired` caught, returns 500 |
| LibreOffice non-zero exit | stderr logged internally, 500 returned |
| Corrupt image file passed to img2pdf | Exception caught, returns 500 |

### Image Extraction & Captioning
| Case | Handling |
|---|---|
| Tiny decorative images (icons, bullets) | Filtered out at < 100×100 px |
| Image with no placement bbox in PDF | `y0` defaults to `0.0` (top of page) |
| VLM API failure for one image | Per-image try/except; other images still captioned |
| No LLM provider configured / missing API key | `get_provider()` returns `None`; captioning skipped, OCR text still returned |
| VLM returns unstructured output (ignores prompt format) | `parse_caption()` regex is section-order-agnostic; `confidence` defaults to `MEDIUM` |
| VLM returns `NONE` in ASCII block | `ascii` field set to `None`, not the string `"NONE"` |
| `[CONFIDENCE: HIGH]` vs `[CONFIDENCE]\nHIGH` format variation | Both matched by the same regex |

### Math & Text Quality
| Case | Handling |
|---|---|
| Garbled mathematical notation from Tesseract | Three-heuristic detector flags affected pages in output and API response |
| Code blocks triggering garbled-math false positives | Heuristic 2 skips lines matching code-line prefix patterns |
| Page with no extractable text | `pdfplumber` returns `""`, stored as empty string, no crash |

### Concurrency & Resources
| Case | Handling |
|---|---|
| Blocking OCR/LibreOffice on async endpoint | `run_in_threadpool` prevents event loop blocking |
| Multiple simultaneous caption API calls | `ThreadPoolExecutor(max_workers=5)` caps concurrency |
| Provider re-initialisation on every request | Singleton cache in `_instances` — one instance per provider per process |
| LibreOffice temp file cleanup | `tempfile.TemporaryDirectory()` context manager guarantees cleanup |

---

## Known Limitations & Future Work

| Limitation | Notes |
|---|---|
| Garbled math in text layer | Heuristic detection only; no correction. Future: MathPix API or dedicated math OCR model |
| No persistent storage | Results are returned in-memory; no caching or job queue |
| Single-process concurrency | Multiple large PDFs processed simultaneously will contend on CPU; future: task queue (Celery/ARQ) |
| Image position uses y0 only | Multi-column layouts may interleave incorrectly; future: use x0 for column-aware sorting |
| No authentication | All endpoints are open; suitable for internal/MCP use only without an auth layer |
