import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.api.controller import AdImageController
from app.api.dependencies import get_controller
from app.core.config import Settings
from app.main import app
from app.services.openai_image_service import RenderResult, plan_size


def make_image(width: int = 800, height: int = 800, fmt: str = "PNG") -> bytes:
    """A small solid-colour image in the requested format."""
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (30, 90, 160)).save(buffer, format=fmt)
    return buffer.getvalue()


class StubImageModel:
    """Stands in for the OpenAI edit endpoint.

    Honours the real size planner so tests exercise genuine resize behaviour,
    and records the prompt so it can be asserted against.
    """

    def __init__(self, model: str = "gpt-image-2") -> None:
        self.model = model
        self.last_prompt: str | None = None
        self.calls = 0

    async def render(self, *, image_png, prompt, width, height) -> RenderResult:
        self.calls += 1
        self.last_prompt = prompt
        plan = plan_size(self.model, width, height)
        buffer = io.BytesIO()
        Image.new("RGB", (plan.width, plan.height), (200, 40, 40)).save(
            buffer, format="PNG"
        )
        return RenderResult(
            image_bytes=buffer.getvalue(), size_plan=plan, model=self.model
        )


@pytest.fixture
def stub_model() -> StubImageModel:
    return StubImageModel()


@pytest.fixture
def client(stub_model: StubImageModel):
    settings = Settings(openai_api_key="test-key")
    controller = AdImageController(stub_model, settings)
    app.dependency_overrides[get_controller] = lambda: controller
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def valid_source_text() -> str:
    return (
        "Handmade sourdough baked fresh every morning at our downtown bakery. "
        "Call us to reserve a loaf."
    )
