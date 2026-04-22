from fastapi import FastAPI, UploadFile, HTTPException
from pipeline import pipeline
from schemas import OCRResponse

app = FastAPI()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/ocr", response_model=OCRResponse)
async def ocr(file: UploadFile):
    if file.content_type != "application/pdf":
        raise HTTPException(status_code=422, detail="Only PDF files are accepted")

    pdf_bytes = await file.read()
    if not pdf_bytes:
        raise HTTPException(status_code=400, detail="Empty file")

    result = pipeline.invoke({
        "pdf_bytes": pdf_bytes,
        "ocr_output_bytes": None,
        "extracted_text": None,
        "error": None,
        "status": "pending",
    })

    if result.get("error"):
        raise HTTPException(status_code=422, detail=result["error"])

    return OCRResponse(
        text=result["extracted_text"],
        page_count=result["page_count"],
        status=result["status"],
    )
