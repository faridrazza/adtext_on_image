import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.api.controller import AdImageController
from app.api.dependencies import get_controller
from app.core.config import Settings
from app.main import app
from app.services.copy_service import AdCopy, CopyOptionSet, Placement
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

    def __init__(self, model: str = "gpt-image-2", quality: str = "low") -> None:
        self.model = model
        self.default_quality = quality
        self.last_prompt: str | None = None
        self.last_quality: str | None = None
        self.calls = 0

    async def render(
        self, *, image_png, prompt, width, height, quality=None
    ) -> RenderResult:
        self.calls += 1
        self.last_prompt = prompt
        self.last_quality = quality or self.default_quality
        plan = plan_size(self.model, width, height)
        buffer = io.BytesIO()
        Image.new("RGB", (plan.width, plan.height), (200, 40, 40)).save(
            buffer, format="PNG"
        )
        return RenderResult(
            image_bytes=buffer.getvalue(),
            size_plan=plan,
            model=self.model,
            quality=self.last_quality,
        )


class StubCopyModel:
    """Stands in for the copywriter. Returns short copy, records its inputs."""

    def __init__(self, headline: str = "Fresh Colour, Flawless Finish") -> None:
        self.model = "stub-copy-model"
        self.headline = headline
        self.subheadline: str | None = None
        self.placement = Placement.BOTTOM_LEFT
        self.last_source_text: str | None = None
        self.calls = 0
        self.option_calls = 0
        # Mirrors the real service: some options carry a supporting line and
        # some do not. In the 49-asset batch run, 14 did and 35 did not.
        self.options: list[tuple[str, str | None]] = [
            ("Fresh Colour, Flawless Finish", "Careful prep, clean edges"),
            ("Colour That Lasts", None),
            ("Every Wall, Considered", None),
        ]

    async def write(self, *, image_png, source_text, spec, width, height) -> AdCopy:
        self.calls += 1
        self.last_source_text = source_text
        return AdCopy(
            headline=self.headline,
            subheadline=self.subheadline,
            placement=self.placement,
            source_support=source_text[:60],
        )

    async def write_options(
        self, *, image_png, source_text, spec, width, height, count=3
    ) -> CopyOptionSet:
        self.option_calls += 1
        self.last_source_text = source_text
        return CopyOptionSet(
            options=[
                AdCopy(
                    headline=headline,
                    subheadline=subheadline,
                    placement=self.placement,
                    source_support=source_text[:60],
                )
                for headline, subheadline in self.options[:count]
            ],
            rejected=[],
        )


@pytest.fixture
def stub_model() -> StubImageModel:
    return StubImageModel()


@pytest.fixture
def stub_copy() -> StubCopyModel:
    return StubCopyModel()


@pytest.fixture
def client(stub_model: StubImageModel, stub_copy: StubCopyModel):
    settings = Settings(openai_api_key="test-key")
    controller = AdImageController(stub_model, stub_copy, settings)
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
