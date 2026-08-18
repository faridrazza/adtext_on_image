import base64
import io

import pytest
from PIL import Image

from tests.conftest import make_image

ENDPOINT = "/api/v1/ad-images/render"

SOURCE_TEXT = (
    "Handmade sourdough baked fresh every morning at our downtown bakery. "
    "Call us to reserve a loaf."
)


def post(client, *, image=None, **overrides):
    """Post a render request. width/height are omitted unless asked for."""
    data = {
        "source_text": SOURCE_TEXT,
        "platform": "meta",
        "asset_type": "feed_square",
    }
    data.update(overrides)
    return client.post(
        ENDPOINT,
        files={"image": ("source.png", image or make_image(), "image/png")},
        data=data,
    )


# --- meta endpoints --------------------------------------------------------


def test_health(client):
    assert client.get("/api/v1/health").json() == {"status": "ok"}


def test_demo_console_is_served_at_the_root(client):
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "Ad Asset Bench" in response.text


def test_capabilities_lists_every_platform(client):
    body = client.get("/api/v1/capabilities").json()
    assert {p["platform"] for p in body["platforms"]} == {
        "google_ads_pmax",
        "meta",
        "google_business_profile",
        "website",
    }


def test_capabilities_exposes_default_sizes(client):
    body = client.get("/api/v1/capabilities").json()
    pmax = next(p for p in body["platforms"] if p["platform"] == "google_ads_pmax")
    square = next(a for a in pmax["asset_types"] if a["asset_type"] == "square")
    assert (square["default_width"], square["default_height"]) == (1200, 1200)
    assert "WEBP" not in square["allowed_formats"]
    assert square["accepts_text_overlay"] is True


def test_capabilities_flags_logo_assets_as_text_free(client):
    body = client.get("/api/v1/capabilities").json()
    pmax = next(p for p in body["platforms"] if p["platform"] == "google_ads_pmax")
    logo = next(a for a in pmax["asset_types"] if a["asset_type"] == "logo_square")
    assert logo["accepts_text_overlay"] is False


# --- dimensions derived from platform + asset type -------------------------


@pytest.mark.parametrize(
    ("platform", "asset_type", "expected"),
    [
        ("google_ads_pmax", "square", (1200, 1200)),
        ("google_ads_pmax", "landscape", (1200, 628)),
        ("google_ads_pmax", "portrait", (1200, 1500)),
        ("meta", "feed_square", (1080, 1080)),
        ("meta", "feed_portrait", (1080, 1350)),
        ("meta", "story_reel", (1080, 1920)),
        ("meta", "facebook_landscape", (1200, 630)),
        ("google_business_profile", "photo", (1080, 1080)),
        ("website", "hero", (1920, 1080)),
        ("website", "section", (1200, 800)),
        ("website", "sidebar_card", (300, 250)),
    ],
)
def test_output_size_is_derived_from_the_asset_type(
    client, platform, asset_type, expected
):
    body = post(client, platform=platform, asset_type=asset_type).json()
    assert (body["image"]["width"], body["image"]["height"]) == expected
    assert (body["asset"]["output_width"], body["asset"]["output_height"]) == expected
    assert body["asset"]["dimension_source"] == "platform_default"

    decoded = base64.b64decode(body["image"]["b64"])
    with Image.open(io.BytesIO(decoded)) as img:
        assert img.size == expected


def test_explicit_dimensions_override_the_default(client):
    body = post(client, width=1440, height=1440).json()
    assert (body["image"]["width"], body["image"]["height"]) == (1440, 1440)
    assert body["asset"]["dimension_source"] == "request"


def test_width_without_height_is_refused(client, stub_model):
    response = post(client, width=1200)
    assert response.status_code == 422
    assert response.json()["code"] == "invalid_request"
    assert stub_model.calls == 0


# --- response shape --------------------------------------------------------


def test_response_reports_the_source_image_and_asset(client):
    body = post(client, image=make_image(640, 480)).json()
    assert body["source_image"]["width"] == 640
    assert body["source_image"]["height"] == 480
    assert body["asset"]["label"] == "Feed square (1:1)"
    assert body["model"] == "gpt-image-2"


def test_image_payload_is_self_consistent(client):
    body = post(client).json()
    decoded = base64.b64decode(body["image"]["b64"])
    assert body["image"]["size_bytes"] == len(decoded)
    assert body["image"]["media_type"].startswith("image/")


def test_every_response_carries_the_rendering_notice(client):
    assert "generative image model" in post(client).json()["rendering_notice"]


def test_source_text_reaches_the_model_prompt(client, stub_model):
    post(client)
    assert stub_model.calls == 1
    assert SOURCE_TEXT in stub_model.last_prompt
    assert "RULE 2" in stub_model.last_prompt


def test_alt_text_is_returned_only_where_the_platform_requires_it(client):
    website = post(client, platform="website", asset_type="hero").json()
    assert website["alt_text"] == (
        "Handmade sourdough baked fresh every morning at our downtown bakery."
    )
    assert post(client).json()["alt_text"] is None


# --- warnings --------------------------------------------------------------


def test_off_spec_override_is_reported_as_a_warning(client):
    body = post(
        client, platform="google_ads_pmax", asset_type="square", width=800, height=800
    ).json()
    assert any("minimum" in w for w in body["warnings"])


def test_non_aligned_size_reports_the_resample(client):
    # 1080 is not divisible by 16, so gpt-image-2 renders 1088 and downsamples.
    body = post(client).json()
    assert any("1088x1088" in w for w in body["warnings"])
    assert body["image"]["width"] == 1080


def test_platform_default_size_raises_no_dimension_warnings(client):
    body = post(client, platform="website", asset_type="hero").json()
    assert not any("minimum" in w or "Aspect ratio" in w for w in body["warnings"])


# --- rejections ------------------------------------------------------------


def test_logo_assets_are_refused(client):
    response = post(client, platform="google_ads_pmax", asset_type="logo_square")
    assert response.status_code == 422
    assert response.json()["code"] == "unsupported_asset"


def test_asset_type_from_another_platform_is_refused(client):
    response = post(client, platform="meta", asset_type="hero")
    assert response.status_code == 422
    assert response.json()["code"] == "unsupported_asset"


@pytest.mark.parametrize("text", ["   ", "Bakery open"])
def test_insufficient_source_text_is_refused(client, stub_model, text):
    response = post(client, source_text=text)
    assert response.status_code == 422
    assert response.json()["code"] == "insufficient_source_text"
    assert stub_model.calls == 0  # never reaches the paid call


def test_non_image_upload_is_refused(client, stub_model):
    response = post(client, image=b"this is not an image")
    assert response.status_code == 422
    assert response.json()["code"] == "invalid_image"
    assert stub_model.calls == 0


def test_unknown_platform_is_refused(client):
    assert post(client, platform="tiktok").status_code == 422


@pytest.mark.parametrize(("width", "height"), [(10, 10), (99999, 1080)])
def test_out_of_range_dimensions_are_refused(client, width, height):
    response = post(client, width=width, height=height)
    assert response.status_code == 422
    assert response.json()["code"] == "invalid_request"
