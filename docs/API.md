# API Reference — OCR PDF Service

Base URL: `http://localhost:8000` (local) or your Render deployment URL.

Interactive docs (Swagger UI): `GET /docs`
OpenAPI spec: `GET /openapi.json`

---

## Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/health` | Service liveness check |
| POST | `/ocr` | OCR a file, returns JSON |
| POST | `/ocr/download` | OCR a file, returns downloadable file |
| POST | `/ocr-base64` | OCR a base64-encoded file, returns JSON |

---

## GET `/health`

Returns service status. Use for Docker/Render health checks.

**Response**
```json
{"status": "ok"}
```

**Example**
```bash
curl http://localhost:8000/health
```

---

## POST `/ocr`

Upload a file for OCR. Returns structured JSON with extracted text, image captions, and positional Markdown.

**Request**

| Parameter | Type | Location | Required | Description |
|---|---|---|---|---|
| `file` | file | form-data | ✅ | Document to process |
| `provider` | string | query | ❌ | VLM provider: `gemini` or `groq`. Defaults to `LLM_PROVIDER` env var, then `gemini` |

**Supported file types**

| Format | MIME type |
|---|---|
| PDF | `application/pdf` |
| PNG | `image/png` |
| JPEG | `image/jpeg` |
| TIFF | `image/tiff` |
| BMP | `image/bmp` |
| GIF | `image/gif` |
| WebP | `image/webp` |
| Word (.docx) | `application/vnd.openxmlformats-officedocument.wordprocessingml.document` |
| Word (.doc) | `application/msword` |
| PowerPoint (.pptx) | `application/vnd.openxmlformats-officedocument.presentationml.presentation` |
| PowerPoint (.ppt) | `application/vnd.ms-powerpoint` |

**Response — `OCRResponse`**

```json
{
  "text": "Full extracted text from all pages joined by newlines...",
  "page_count": 5,
  "status": "merged",
  "merged_content": "## Page 1\n\nIntroduction to Graph Neural Networks...\n\n[IMAGE 1 - Page 1]\n[ASCII]\nA ---[msg]--> B\n[DESCRIPTION]\nMessage passing between two nodes.\n[CONFIDENCE: HIGH]\n\n## Page 2\n...",
  "garbled_math_pages": [3, 4],
  "image_captions": [
    {
      "page": 1,
      "caption": "[ASCII]\nA ---[msg]--> B\n[DESCRIPTION]\nMessage passing between two nodes.\n[CONFIDENCE: HIGH]",
      "ascii": "A ---[msg]--> B",
      "description": "Message passing between two nodes.",
      "confidence": "HIGH"
    }
  ]
}
```

| Field | Type | Description |
|---|---|---|
| `text` | string | Raw OCR text, all pages joined |
| `page_count` | integer | Total number of pages |
| `status` | string | Final pipeline status (`merged`, `failed`, etc.) |
| `merged_content` | string \| null | Positional Markdown with text and images interleaved by vertical position |
| `garbled_math_pages` | int[] \| null | 1-indexed page numbers where math notation may be garbled by OCR |
| `image_captions` | CaptionItem[] \| null | Structured per-image captions (see below) |

**`CaptionItem` fields**

| Field | Type | Description |
|---|---|---|
| `page` | integer | Page number the image was found on |
| `caption` | string | Full raw VLM output (preserved) |
| `ascii` | string \| null | ASCII art reproduction. `null` if image is decorative/photo |
| `description` | string | 2–4 sentence semantic explanation |
| `confidence` | string | `HIGH`, `MEDIUM`, or `LOW` — VLM's self-rated ASCII accuracy |

**Examples**

```bash
# Basic OCR
curl -X POST http://localhost:8000/ocr \
  -F "file=@document.pdf"

# Use Groq instead of Gemini for image captioning
curl -X POST "http://localhost:8000/ocr?provider=groq" \
  -F "file=@document.pdf"

# OCR a scanned image
curl -X POST http://localhost:8000/ocr \
  -F "file=@scanned_page.png"

# OCR a Word document
curl -X POST http://localhost:8000/ocr \
  -F "file=@report.docx"

# Save JSON response to file
curl -X POST http://localhost:8000/ocr \
  -F "file=@document.pdf" \
  -o result.json
```

**Error responses**

| Status | Condition |
|---|---|
| 400 | Empty file |
| 413 | File exceeds 50 MB |
| 422 | Unsupported file type (MIME detected from file content, not filename) |
| 500 | OCR engine failure, conversion failure, or pipeline error |

---

## POST `/ocr/download`

Same processing as `/ocr` but returns the result as a downloadable file instead of JSON. The filename is derived from the uploaded file's name.

**Request**

| Parameter | Type | Location | Required | Description |
|---|---|---|---|---|
| `file` | file | form-data | ✅ | Document to process |
| `provider` | string | query | ❌ | VLM provider: `gemini` or `groq` |
| `format` | string | query | ❌ | Output format: `txt`, `md`, `json`. Default: `txt` |

**Output formats**

| `format` | Content-Type | Content | Filename |
|---|---|---|---|
| `txt` | `text/plain` | `merged_content` (falls back to `text`) | `{original_name}.txt` |
| `md` | `text/markdown` | `merged_content` (falls back to `text`) — full positional Markdown | `{original_name}.md` |
| `json` | `application/json` | Full `OCRResponse` as pretty-printed JSON | `{original_name}.json` |

**Examples**

```bash
# Download as plain text (curl -O uses server filename, -J uses Content-Disposition header)
curl -X POST http://localhost:8000/ocr/download \
  -F "file=@report.pdf" \
  -O -J
# saves as: report.txt

# Download as Markdown with images embedded at their original position
curl -X POST "http://localhost:8000/ocr/download?format=md" \
  -F "file=@paper.pdf" \
  -O -J
# saves as: paper.md

# Download full JSON response
curl -X POST "http://localhost:8000/ocr/download?format=json" \
  -F "file=@paper.pdf" \
  -O -J
# saves as: paper.json

# Specify output filename manually
curl -X POST "http://localhost:8000/ocr/download?format=md" \
  -F "file=@paper.pdf" \
  -o my_output.md
```

**Markdown output structure**

The `.md` output interleaves text and images sorted by their vertical position on each page, so the document reads in the same order as the original:

```markdown
## Page 1

This is the first paragraph extracted from the page.
Another line of text that appeared below it.

[IMAGE 1 - Page 1]
[ASCII]
INPUT --> HIDDEN_LAYER --> OUTPUT

[DESCRIPTION]
A simple feedforward neural network with one hidden layer.
Arrows represent weighted connections between neurons.

[CONFIDENCE: HIGH]

Text that appeared below the image in the original document.

## Page 2  ⚠️ _Math equations on this page may be garbled — manual review recommended_

...
```

Images with low VLM confidence are flagged inline:
```
[IMAGE 2 - Page 4] ⚠️ LOW CONFIDENCE
```

---

## POST `/ocr-base64`

OCR a file sent as a base64-encoded string in a JSON body. Useful for MCP tool integrations and programmatic clients where multipart form uploads are inconvenient.

**Request body**

```json
{
  "file_base64": "<base64-encoded file bytes>"
}
```

The field `pdf_base64` is also accepted as an alias for `file_base64` (backwards compatibility).

| Parameter | Type | Location | Required | Description |
|---|---|---|---|---|
| `file_base64` | string | JSON body | ✅ | Base64-encoded file content |
| `provider` | string | query | ❌ | VLM provider: `gemini` or `groq` |

**Response**

Same `OCRResponse` structure as `/ocr`.

**Examples**

```bash
# Encode and send a PDF
curl -X POST http://localhost:8000/ocr-base64 \
  -H "Content-Type: application/json" \
  -d "{\"file_base64\": \"$(base64 -w 0 document.pdf)\"}"
```

```python
import base64, httpx

with open("document.pdf", "rb") as f:
    encoded = base64.b64encode(f.read()).decode()

response = httpx.post(
    "http://localhost:8000/ocr-base64",
    json={"file_base64": encoded}
)
data = response.json()
print(data["merged_content"])
```

```javascript
const fs = require("fs");
const encoded = fs.readFileSync("document.pdf").toString("base64");

const res = await fetch("http://localhost:8000/ocr-base64", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ file_base64: encoded }),
});
const data = await res.json();
console.log(data.merged_content);
```

**Error responses**

| Status | Condition |
|---|---|
| 422 | Missing `file_base64` field |
| 422 | Invalid base64 string |
| 413 | Decoded file exceeds 50 MB |
| 422 | Unsupported file type |
| 500 | Pipeline error |

---

## Notes

**MIME detection** — The service detects file type from the file's magic bytes, not the filename or `Content-Type` header. Renaming a `.jpg` to `.pdf` will be detected as an image and processed correctly.

**Image captioning is optional** — If no VLM provider is configured (no `GEMINI_API_KEY` or `GROQ_API_KEY`), the service still returns OCR text. `image_captions` will be an empty list and `merged_content` will contain text only.

**Math garbling** — If `garbled_math_pages` is non-null in the response, those pages contain mathematical notation that Tesseract likely mangled. The affected pages are also flagged inline in `merged_content`. Manual review or a dedicated math OCR tool is recommended for those pages.

**Provider selection priority**
1. `?provider=` query parameter on the request
2. `LLM_PROVIDER` environment variable
3. Default: `gemini`
