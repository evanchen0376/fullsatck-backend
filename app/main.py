from fastapi import FastAPI

from app.api.exception_handlers import app_exception_handler
from app.api.router import api_router

app = FastAPI(title="MiniShop")

app.add_exception_handler(AppException, app_exception_handler)
app.include_router(
    api_router,
    prefix="/api/v1",
)


@app.get("/")
async def root():
    return {"message": "MiniShop API is running"}
