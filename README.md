# OCR PDF API Service

## 1. Local Run

```bash
cd ocr-service
docker build -t ocr-service .
docker run -p 8000:8000 ocr-service
```

## 2. Test Endpoints

```bash
# Health check
curl http://localhost:8000/health

# OCR a PDF
curl -X POST http://localhost:8000/ocr \
  -F "file=@scanned.pdf"
```

## 3. Claude Desktop Setup

1. Copy `claude_mcp_config.json` contents into your Claude Desktop config (`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS).
2. Replace `/ABSOLUTE/PATH/TO/` with the real path to `mcp_server.py`.
3. Ensure `mcp` and `httpx` are installed: `pip install mcp httpx`
4. Restart Claude Desktop.

## 4. Deploy to Render

1. Push this repo to GitHub.
2. Go to [render.com](https://render.com) → New → Blueprint.
3. Connect your GitHub repo — Render will detect `render.yaml` automatically.
4. Click **Apply** and wait for the build.

## 5. Consuming as REST API

- Interactive docs: `http://localhost:8000/docs`
- OpenAPI spec: `http://localhost:8000/openapi.json`

## 6. Image Captioning

Embedded images in the PDF are extracted, captioned via Gemini Vision, and merged with OCR text into `merged_content`.

**Get a Gemini API key:** [aistudio.google.com](https://aistudio.google.com)

**Local:**
```bash
export GEMINI_API_KEY=your_key
uvicorn main:app --host 0.0.0.0 --port 8000
```

**Docker:**
```bash
docker run --env GEMINI_API_KEY=your_key -p 8000:8000 ocr-service
```

**Render:** Environment tab → add `GEMINI_API_KEY` with your key value.
