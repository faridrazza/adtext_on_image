import pytest

from app.core.errors import UnsupportedAssetError
from app.domain import platforms
from app.domain.platforms import AssetType, Platform


def test_resolves_known_asset():
    spec = platforms.resolve(Platform.META, AssetType.STORY_REEL)
    assert spec.recommended_size == (1080, 1920)
    assert spec.max_bytes == 30 * 1024 * 1024


def test_rejects_asset_type_from_another_platform():
    with pytest.raises(UnsupportedAssetError) as exc:
        platforms.resolve(Platform.META, AssetType.HERO)
    assert "feed_square" in exc.value.details["valid_asset_types"]


def test_on_spec_dimensions_produce_no_warnings():
    spec = platforms.resolve(Platform.META, AssetType.FEED_PORTRAIT)
    assert platforms.validate_dimensions(spec, 1080, 1350) == []


def test_below_minimum_dimensions_warn():
    spec = platforms.resolve(Platform.GOOGLE_ADS_PMAX, AssetType.SQUARE)
    warnings = platforms.validate_dimensions(spec, 800, 800)
    assert any("minimum" in w for w in warnings)


def test_wrong_aspect_ratio_warns():
    spec = platforms.resolve(Platform.META, AssetType.STORY_REEL)
    warnings = platforms.validate_dimensions(spec, 1080, 1080)
    assert any("Aspect ratio" in w for w in warnings)


def test_pmax_excludes_webp():
    spec = platforms.resolve(Platform.GOOGLE_ADS_PMAX, AssetType.SQUARE)
    assert "WEBP" not in spec.allowed_formats


def test_website_allows_webp_and_requires_alt_text():
    spec = platforms.resolve(Platform.WEBSITE, AssetType.HERO)
    assert "WEBP" in spec.allowed_formats
    assert spec.requires_alt_text


@pytest.mark.parametrize(
    "asset_type", [AssetType.LOGO_SQUARE, AssetType.LOGO_WIDE]
)
def test_logo_assets_forbid_text_overlay(asset_type):
    spec = platforms.resolve(Platform.GOOGLE_ADS_PMAX, asset_type)
    assert spec.allows_text_overlay is False
    assert spec.requires_transparency is True


def test_every_platform_exposes_asset_types():
    for platform in Platform:
        assert platforms.asset_types_for(platform)


def test_every_asset_type_has_a_default_size():
    """Platform + asset type must always be enough to derive an output size."""
    for platform in Platform:
        for spec in platforms.specs_for(platform):
            width, height = spec.default_size
            assert width > 0 and height > 0


def test_default_size_prefers_the_first_published_size():
    spec = platforms.resolve(Platform.WEBSITE, AssetType.HERO)
    assert spec.exact_sizes == ((1920, 1080), (1920, 600))
    assert spec.default_size == (1920, 1080)


def test_section_image_falls_back_to_a_documented_house_default():
    # The platform publishes only a minimum width, so a default is supplied.
    spec = platforms.resolve(Platform.WEBSITE, AssetType.SECTION)
    assert spec.exact_sizes == ()
    assert spec.default_size == (1200, 800)
    assert spec.default_size[0] >= spec.min_width
    assert any("backend default" in note for note in spec.notes)
