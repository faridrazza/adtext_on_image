"""Application assembly: app instance, middleware, error handling, routes."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from app.api.router import router
from app.api.schemas import ErrorResponse
from app.core.config import get_settings
from app.core.errors import AdImageError

# The demo console, served same-origin so it can call the API without CORS
# setup. It drives the two-step flow -- three copy options, then a render of
# the chosen words -- which is the flow the API supports.
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
STUDIO_UI = STATIC_DIR / "studio.html"


def create_app() -> FastAPI:
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    app = FastAPI(
        title=settings.app_name,
        version="1.0.0",
        description=(
            "Sets platform-appropriate ad text over an existing image. Text "
            "only -- no calls-to-action, logos, icons or graphics are added. "
            "The supplied source text is the only permitted basis for the copy."
        ),
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(AdImageError)
    async def handle_ad_image_error(
        _: Request, exc: AdImageError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=ErrorResponse(
                code=exc.code, message=exc.message, details=exc.details
            ).model_dump(),
        )

    app.include_router(router)

    if STUDIO_UI.is_file():

        # Served at both paths so an old bookmark still lands somewhere useful.
        @app.get("/", include_in_schema=False)
        @app.get("/studio", include_in_schema=False)
        async def studio_ui() -> FileResponse:
            return FileResponse(STUDIO_UI)

    return app


app = create_app()
