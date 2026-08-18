"""Wiring for the API layer.

The controller and its OpenAI client are built once and reused, so each request
does not pay client-construction cost.
"""

from __future__ import annotations

from app.api.controller import AdImageController
from app.core.config import get_settings
from app.services.openai_image_service import OpenAIImageService

_controller: AdImageController | None = None


def get_controller() -> AdImageController:
    global _controller
    if _controller is None:
        settings = get_settings()
        _controller = AdImageController(OpenAIImageService(settings), settings)
    return _controller
