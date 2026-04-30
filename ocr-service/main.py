import logging

from fastapi import FastAPI

from routers.ocr import router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

app = FastAPI()
app.include_router(router)
