# API Reference — OCR PDF Service

Base URL: `http://localhost:8000` (local) or your Render deployment URL.

Interactive docs (Swagger UI): `GET /docs`
OpenAPI spec: `GET /openapi.json`

---

## Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/health` | Service liveness check |
| GET | `/supported-formats` | List accepted file types and size limit |
| POST | `/validate` | Check a file without running OCR |
| POST | `/ocr` | OCR a file, returns JSON |
| POST | `/ocr/download` | OCR a file, returns downloadable file |
| POST | `/ocr-base64` | OCR a base64-encoded file, returns JSON |

---

## Authentication

No server-side API key is required. Pass your own VLM provider key per request via headers:

| Header | Provider |
|---|---|
| `x-gemini-api-key` | Gemini (default) |
| `x-groq-api-key` | Groq |

If no header key is sent, the server falls back to its own `GEMINI_API_KEY` / `GROQ_API_KEY` env vars (if set). If neither is available, OCR still runs but image captioning is skipped.

---

## GET `/health`

Returns service status. Used for Docker/Render health checks.

**Response**
```json
{"status": "ok"}
```

**Example**
```bash
curl http://localhost:8000/health
```

---

## GET `/supported-formats`

Returns the file types and size limit accepted by the service. Call this before uploading to check compatibility.

**Response**
```json
{
  "supported": ["BMP image", "GIF image", "JPEG image", "PDF", "PNG image", "TIFF image", "WebP image"],
  "max_file_size_mb": 50
}
```

**Example**
```bash
curl http://localhost:8000/supported-formats
```

---

## POST `/validate`

Check whether a file is supported without running OCR. Reads only the first 4 KB for MIME detection — fast and cheap.

**Request**

| Parameter | Type | Location | Required |
|---|---|---|---|
| `file` | file | form-data | ✅ |

**Response — valid file**
```json
{"valid": true, "detected_mime": "application/pdf", "filename": "paper.pdf"}
```

**Response — invalid file**
```json
{
  "valid": false,
  "reason": "\"report.xlsx\" appears to be a XLSX file, which is not supported. Please upload one of the following: BMP image, GIF image, JPEG image, PDF, PNG image, TIFF image, WebP image."
}
```

**Example**
```bash
curl -X POST http://localhost:8000/validate -F "file=@document.pdf"
```

---

## POST `/ocr`

Upload a file for OCR. Returns structured JSON with extracted text, image captions, and positional Markdown.

**Request**

| Parameter | Type | Location | Required | Description |
|---|---|---|---|---|
| `file` | file | form-data | ✅ | Document to process |
| `provider` | string | query | ❌ | `gemini` (default) or `groq` |
| `x-gemini-api-key` | string | header | ❌ | Your Gemini API key |
| `x-groq-api-key` | string | header | ❌ | Your Groq API key |

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

**Response — `OCRResponse`**

```json
{
  "text": "Full extracted text from all pages...",
  "page_count": 5,
  "status": "merged",
  "merged_content": "## Page 1\n\nIntroduction...\n\n[IMAGE 1 - Page 1]\n[ASCII]\nA ---[msg]--> B\n[DESCRIPTION]\nMessage passing diagram.\n[CONFIDENCE: HIGH]\n\n## Page 3  ⚠️ _Math equations on this page may be garbled_\n...",
  "garbled_math_pages": [3],
  "image_captions": [
    {
      "page": 1,
      "caption": "[ASCII]\nA ---[msg]--> B\n[DESCRIPTION]\nMessage passing diagram.\n[CONFIDENCE: HIGH]",
      "ascii": "A ---[msg]--> B",
      "description": "Message passing diagram.",
      "confidence": "HIGH"
    }
  ]
}
```

| Field | Type | Description |
|---|---|---|
| `text` | string | Raw OCR text, all pages joined |
| `page_count` | integer | Total number of pages |
| `status` | string | Final pipeline status |
| `merged_content` | string \| null | Positional Markdown — text and images interleaved by vertical position |
| `garbled_math_pages` | int[] \| null | 1-indexed pages where math notation may be garbled by OCR |
| `image_captions` | CaptionItem[] \| null | Structured per-image captions |

**`CaptionItem` fields**

| Field | Type | Description |
|---|---|---|
| `page` | integer | Page number |
| `caption` | string | Full raw VLM output (preserved) |
| `ascii` | string \| null | ASCII art. `null` if image is decorative/photo |
| `description` | string | 2–4 sentence semantic explanation |
| `confidence` | string | `HIGH`, `MEDIUM`, or `LOW` |

**Examples**

```bash
# With your own Gemini key
curl -X POST http://localhost:8000/ocr \
  -H "x-gemini-api-key: AIza..." \
  -F "file=@paper.pdf"

# Use Groq instead
curl -X POST "http://localhost:8000/ocr?provider=groq" \
  -H "x-groq-api-key: gsk_..." \
  -F "file=@paper.pdf"

# OCR only (no captioning)
curl -X POST http://localhost:8000/ocr \
  -F "file=@scanned.png"

# Save JSON response
curl -X POST http://localhost:8000/ocr \
  -H "x-gemini-api-key: AIza..." \
  -F "file=@paper.pdf" \
  -o result.json
```

**Error responses**

| Status | Message example |
|---|---|
| 422 | `"The uploaded file is empty. Please upload a valid document."` |
| 422 | `"File is too large (67.3 MB). Maximum allowed size is 50 MB."` |
| 422 | `"\"report.xlsx\" appears to be a XLSX file, which is not supported. Please upload one of the following: ..."` |
| 500 | OCR engine or pipeline failure |

---

## POST `/ocr/download`

Same processing as `/ocr` but returns the result as a downloadable file. Filename is derived from the uploaded file's name.

**Request**

| Parameter | Type | Location | Required | Description |
|---|---|---|---|---|
| `file` | file | form-data | ✅ | Document to process |
| `provider` | string | query | ❌ | `gemini` or `groq` |
| `format` | string | query | ❌ | `txt` (default), `md`, or `json` |
| `x-gemini-api-key` | string | header | ❌ | Your Gemini API key |
| `x-groq-api-key` | string | header | ❌ | Your Groq API key |

**Output formats**

| `format` | Content-Type | Content | Filename |
|---|---|---|---|
| `txt` | `text/plain` | `merged_content` (falls back to `text`) | `{name}.txt` |
| `md` | `text/markdown` | Positional Markdown with images at origin position | `{name}.md` |
| `json` | `application/json` | Full `OCRResponse` as pretty-printed JSON | `{name}.json` |

**Examples**

```bash
# Download as Markdown (curl -O -J uses server-provided filename)
curl -X POST "http://localhost:8000/ocr/download?format=md" \
  -H "x-gemini-api-key: AIza..." \
  -F "file=@paper.pdf" \
  -O -J
# saves as: paper.md

# Download as plain text
curl -X POST http://localhost:8000/ocr/download \
  -H "x-gemini-api-key: AIza..." \
  -F "file=@paper.pdf" \
  -O -J
# saves as: paper.txt

# Download full JSON
curl -X POST "http://localhost:8000/ocr/download?format=json" \
  -H "x-gemini-api-key: AIza..." \
  -F "file=@paper.pdf" \
  -O -J
# saves as: paper.json

# Specify output path manually
curl -X POST "http://localhost:8000/ocr/download?format=md" \
  -H "x-gemini-api-key: AIza..." \
  -F "file=@paper.pdf" \
  -o ~/Downloads/paper.md
```

**Markdown output structure**

Text and images are sorted by vertical position on each page — the document reads in the same order as the original PDF:

```markdown
## Page 1

First paragraph of text on the page.
Second line of text.

[IMAGE 1 - Page 1]
[ASCII]
INPUT --> HIDDEN_LAYER --> OUTPUT

[DESCRIPTION]
A feedforward neural network with one hidden layer.

[CONFIDENCE: HIGH]

Text that appeared below the image in the original.

## Page 3  ⚠️ _Math equations on this page may be garbled — manual review recommended_

## Page 5

[IMAGE 1 - Page 5] ⚠️ LOW CONFIDENCE
[ASCII]
...
```

---

## POST `/ocr-base64`

OCR a file sent as a base64-encoded string. Useful for MCP tool integrations and programmatic clients where multipart uploads are inconvenient.

**Request body**

```json
{"file_base64": "<base64-encoded file bytes>"}
```

`pdf_base64` is also accepted as an alias for backwards compatibility.

**Request**

| Parameter | Type | Location | Required | Description |
|---|---|---|---|---|
| `file_base64` | string | JSON body | ✅ | Base64-encoded file |
| `provider` | string | query | ❌ | `gemini` or `groq` |
| `x-gemini-api-key` | string | header | ❌ | Your Gemini API key |
| `x-groq-api-key` | string | header | ❌ | Your Groq API key |

**Response:** same `OCRResponse` as `/ocr`.

**Examples**

```bash
curl -X POST http://localhost:8000/ocr-base64 \
  -H "Content-Type: application/json" \
  -H "x-gemini-api-key: AIza..." \
  -d "{\"file_base64\": \"$(base64 -w 0 paper.pdf)\"}"
```

```python
import base64, httpx

with open("paper.pdf", "rb") as f:
    encoded = base64.b64encode(f.read()).decode()

response = httpx.post(
    "http://localhost:8000/ocr-base64",
    headers={"x-gemini-api-key": "AIza..."},
    json={"file_base64": encoded},
)
print(response.json()["merged_content"])
```

```javascript
const fs = require("fs");
const encoded = fs.readFileSync("paper.pdf").toString("base64");

const res = await fetch("http://localhost:8000/ocr-base64", {
  method: "POST",
  headers: {
    "Content-Type": "application/json",
    "x-gemini-api-key": "AIza...",
  },
  body: JSON.stringify({ file_base64: encoded }),
});
console.log((await res.json()).merged_content);
```

**Error responses**

| Status | Message |
|---|---|
| 422 | `"Missing 'file_base64' field in request body."` |
| 422 | `"Invalid base64 encoding. Please re-encode your file and try again."` |
| 422 | File too large or unsupported type (same friendly messages as `/ocr`) |
| 500 | Pipeline error |

---

## Notes

**MIME detection** — File type is detected from magic bytes, not the filename or `Content-Type` header. A `.pdf` file that is actually a Word document will be rejected with a clear message.

**Image captioning is optional** — If no API key is provided (header or server env var), OCR still runs and returns text. `image_captions` will be an empty list.

**`garbled_math_pages`** — When non-null, those pages contain mathematical notation that Tesseract likely mangled. They are also flagged inline in `merged_content`. Manual review is recommended for those pages.

**Provider selection priority**
1. `?provider=` query param
2. `LLM_PROVIDER` environment variable on the server
3. Default: `gemini`
