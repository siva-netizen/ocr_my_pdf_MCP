from fastapi import FastAPI, UploadFile, HTTPException
from pipeline import pipeline
from schemas import OCRResponse

app = FastAPI()


@app.get("/health")
def health():
    return {"status": "ok"}


def _invoke(pdf_bytes: bytes) -> OCRResponse:
    result = pipeline.invoke({
        "pdf_bytes": pdf_bytes,
        "ocr_output_bytes": None,
        "extracted_text": None,
        "extracted_images": None,
        "image_captions": None,
        "merged_content": None,
        "page_count": None,
        "error": None,
        "status": "pending",
    })
    if result.get("error"):
        raise HTTPException(status_code=500, detail=result["error"])
    return OCRResponse(
        text=result["extracted_text"],
        page_count=result["page_count"],
        status=result["status"],
        image_captions=result.get("image_captions"),
        merged_content=result.get("merged_content"),
    )


@app.post("/ocr", response_model=OCRResponse)
async def ocr(file: UploadFile):
    if file.content_type != "application/pdf":
        raise HTTPException(status_code=422, detail="Only PDF files are accepted")
    pdf_bytes = await file.read()
    if not pdf_bytes:
        raise HTTPException(status_code=400, detail="Empty file")
    return _invoke(pdf_bytes)


@app.post("/ocr-base64", response_model=OCRResponse)
async def ocr_base64(payload: dict):
    import base64
    try:
        pdf_bytes = base64.b64decode(payload["pdf_base64"])
    except Exception:
        raise HTTPException(status_code=422, detail="Invalid base64")
    return _invoke(pdf_bytes)
