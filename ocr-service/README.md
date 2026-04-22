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
