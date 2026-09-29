from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db

router = APIRouter()


@router.get("/health")
async def health():
    return {"status": "ok"}


@router.get("/ready")
async def ready(db: AsyncSession = Depends(get_db)):
    try:
        await db.execute(text("SELECT 1"))
    except Exception:
        return {"status": "error"}
    return {"status": "ok"}
