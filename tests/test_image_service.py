import io

import pytest
from PIL import Image

from app.core.errors import InvalidImageError
from app.domain import platforms
from app.domain.platforms import AssetType, Platform
from app.services import image_service
from tests.conftest import make_image

PMAX_LANDSCAPE = platforms.resolve(Platform.GOOGLE_ADS_PMAX, AssetType.LANDSCAPE)
PMAX_SQUARE = platforms.resolve(Platform.GOOGLE_ADS_PMAX, AssetType.SQUARE)
WEBSITE_SECTION = platforms.resolve(Platform.WEBSITE, AssetType.SECTION)
META_SQUARE = platforms.resolve(Platform.META, AssetType.FEED_SQUARE)


# --- decode ----------------------------------------------------------------


@pytest.mark.parametrize("fmt", ["PNG", "JPEG", "WEBP"])
def test_decodes_supported_formats(fmt):
    asset = image_service.decode(make_image(640, 480, fmt))
    assert (asset.width, asset.height) == (640, 480)
    assert asset.image_format == fmt


def test_empty_upload_is_rejected():
    with pytest.raises(InvalidImageError, match="empty"):
        image_service.decode(b"")


def test_non_image_payload_is_rejected():
    with pytest.raises(InvalidImageError):
        image_service.decode(b"definitely not an image at all")


def test_truncated_image_is_rejected():
    with pytest.raises(InvalidImageError):
        image_service.decode(make_image(400, 400, "JPEG")[:120])


def test_detects_alpha_channel():
    buffer = io.BytesIO()
    Image.new("RGBA", (100, 100), (0, 0, 0, 0)).save(buffer, format="PNG")
    assert image_service.decode(buffer.getvalue()).has_alpha


# --- conversion for upload -------------------------------------------------


def test_to_png_bytes_always_yields_png():
    png = image_service.to_png_bytes(make_image(320, 240, "JPEG"))
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    assert image_service.decode(png).image_format == "PNG"


# --- finalize --------------------------------------------------------------


def test_output_is_forced_to_the_requested_dimensions():
    rendered = make_image(1024, 1024, "PNG")
    final, warnings = image_service.finalize(
        rendered, width=1200, height=628, spec=PMAX_LANDSCAPE
    )
    assert (final.width, final.height) == (1200, 628)
    assert any("resampled" in w for w in warnings)


def test_matching_dimensions_produce_no_resample_warning():
    rendered = make_image(1080, 1080, "PNG")
    final, warnings = image_service.finalize(
        rendered, width=1080, height=1080, spec=META_SQUARE
    )
    assert (final.width, final.height) == (1080, 1080)
    assert not any("resampled" in w for w in warnings)


def test_pmax_output_never_uses_webp():
    final, _ = image_service.finalize(
        make_image(1200, 1200, "PNG"), width=1200, height=1200, spec=PMAX_SQUARE
    )
    assert final.image_format in PMAX_SQUARE.allowed_formats
    assert final.image_format != "WEBP"


def test_transparent_input_keeps_an_alpha_capable_format():
    buffer = io.BytesIO()
    Image.new("RGBA", (600, 400), (10, 20, 30, 0)).save(buffer, format="PNG")
    final, _ = image_service.finalize(
        buffer.getvalue(), width=1200, height=800, spec=WEBSITE_SECTION
    )
    assert final.image_format in {"PNG", "WEBP"}


def test_oversized_output_is_compressed_toward_the_platform_ceiling():
    # Noise resists compression, so this exercises the JPEG quality ladder.
    import random

    random.seed(0)
    noisy = Image.new("RGB", (2000, 2000))
    noisy.putdata(
        [
            (random.randint(0, 255), random.randint(0, 255), random.randint(0, 255))
            for _ in range(2000 * 2000)
        ]
    )
    buffer = io.BytesIO()
    noisy.save(buffer, format="PNG")

    final, _ = image_service.finalize(
        buffer.getvalue(), width=1200, height=1200, spec=PMAX_SQUARE
    )
    assert final.image_format == "JPEG"
    # The ladder should have pulled it under the 5MB PMax ceiling.
    assert final.size_bytes <= PMAX_SQUARE.max_bytes


def test_undersized_output_warns_against_the_minimum():
    final, warnings = image_service.finalize(
        make_image(1200, 628, "PNG"), width=1200, height=628, spec=PMAX_LANDSCAPE
    )
    # A flat colour compresses far below the 50KB PMax floor.
    assert final.size_bytes < PMAX_LANDSCAPE.min_bytes
    assert any("below" in w for w in warnings)


def test_unreadable_render_is_reported_as_invalid():
    with pytest.raises(InvalidImageError):
        image_service.finalize(
            b"not an image", width=100, height=100, spec=META_SQUARE
        )


# --- format selection ------------------------------------------------------


@pytest.mark.parametrize(
    ("spec", "needs_alpha", "expected"),
    [
        (PMAX_SQUARE, False, "JPEG"),
        (PMAX_SQUARE, True, "PNG"),
        (WEBSITE_SECTION, False, "JPEG"),
        (META_SQUARE, True, "PNG"),
    ],
)
def test_output_format_selection(spec, needs_alpha, expected):
    assert image_service.choose_output_format(spec, needs_alpha) == expected
