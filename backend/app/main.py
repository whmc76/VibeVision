from fastapi import FastAPI
from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.db.session import SessionLocal, create_db_and_tables
from app.routers import admin, telegram
from app.seed import seed_defaults
from app.services.error_details import format_exception_details

settings = get_settings()

app = FastAPI(title=settings.app_name)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(admin.router, prefix="/api")
app.include_router(telegram.router, prefix="/api")


@app.on_event("startup")
def on_startup() -> None:
    create_db_and_tables()
    with SessionLocal() as db:
        seed_defaults(db, include_demo=settings.environment == "development")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": settings.app_name, "environment": settings.environment}


@app.exception_handler(Exception)
async def unhandled_exception_handler(_request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=500,
        content={"detail": format_exception_details(exc)},
    )
