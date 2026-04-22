from mcp.server.fastmcp import FastMCP
import httpx
import base64

mcp = FastMCP("ocr-service")

OCR_SERVICE_URL = "http://localhost:8000/ocr"


@mcp.tool()
async def ocr_pdf(pdf_base64: str) -> dict:
    """
    Single responsibility: forward base64 PDF to OCR service, return result.
    Input: base64-encoded PDF bytes
    Output: { text: str, page_count: int, status: str }
    """
    pdf_bytes = base64.b64decode(pdf_base64)
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            OCR_SERVICE_URL,
            files={"file": ("upload.pdf", pdf_bytes, "application/pdf")},
        )
        response.raise_for_status()
        return response.json()


if __name__ == "__main__":
    mcp.run()
