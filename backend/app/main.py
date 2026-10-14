"""FastAPI application entry point.

Run (from the backend folder):  uvicorn app.main:app --reload
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api.system import router as system_router
from app.auth.routes import router as auth_router
from app.config import get_settings
from app.db.session import get_engine
from app.logging_config import configure_logging, new_trace_id, trace_id_var
from app.webhooks.routes import router as webhooks_router
from app.worker.processor import Worker

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)
    get_engine()
    if settings.app_env != "test" and settings.jwt_secret == "change-me-in-env":
        log.warning("JWT_SECRET is the default value - set it in backend/.env")
    if settings.meta_app_secret == "change-me-in-env":
        log.warning("META_APP_SECRET is the default value - webhooks from Meta will be rejected")

    worker: Worker | None = None
    if settings.worker_enabled:
        worker = Worker(settings.worker_poll_seconds)
        worker.start()
    app.state.worker = worker
    log.info("app started", extra={"version": __version__, "env": settings.app_env})
    try:
        yield
    finally:
        if worker is not None:
            await worker.stop()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Lead Generation Agent",
        version=__version__,
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173", *settings.extra_cors_origins],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def trace_id_middleware(request: Request, call_next):  # noqa: ANN001, ANN202
        trace_id = request.headers.get("X-Request-ID") or new_trace_id()
        token = trace_id_var.set(trace_id[:32])
        try:
            response = await call_next(request)
        finally:
            trace_id_var.reset(token)
        response.headers["X-Request-ID"] = trace_id[:32]
        return response

    app.include_router(system_router)
    app.include_router(auth_router)
    app.include_router(webhooks_router)
    return app


app = create_app()
