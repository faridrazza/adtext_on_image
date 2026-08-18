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
    assert any("is not a shape" in w for w in warnings)


# --- published sizes must never be reported as wrong ------------------------


def test_every_published_size_validates_cleanly():
    """A platform's own published size can never be off-spec."""
    for platform in Platform:
        for spec in platforms.specs_for(platform):
            for width, height in spec.exact_sizes:
                assert platforms.validate_dimensions(spec, width, height) == [], (
                    f"{platform.value}/{spec.asset_type.value} {width}x{height}"
                )


def test_every_default_size_validates_cleanly():
    for platform in Platform:
        for spec in platforms.specs_for(platform):
            width, height = spec.default_size
            assert platforms.validate_dimensions(spec, width, height) == [], (
                f"{platform.value}/{spec.asset_type.value}"
            )


@pytest.mark.parametrize(
    ("width", "height", "why"),
    [
        (300, 250, "published size"),
        (300, 600, "published size"),
        (400, 400, "1:1 at another scale"),
        (480, 600, "4:5 at another scale"),
        (250, 250, "1:1 at another scale"),
    ],
)
def test_sidebar_card_accepts_all_four_published_options(width, height, why):
    """The spec lists 300x250, 300x600, 1:1 and 4:5 as alternatives."""
    spec = platforms.resolve(Platform.WEBSITE, AssetType.SIDEBAR_CARD)
    assert platforms.validate_dimensions(spec, width, height) == [], why


def test_sidebar_card_still_rejects_an_unlisted_shape():
    spec = platforms.resolve(Platform.WEBSITE, AssetType.SIDEBAR_CARD)
    warnings = platforms.validate_dimensions(spec, 900, 200)  # 4.5:1
    assert any("is not a shape" in w for w in warnings)


def test_gbp_accepts_its_documented_minimum():
    """720x720 is the published minimum, not an off-spec size."""
    spec = platforms.resolve(Platform.GOOGLE_BUSINESS_PROFILE, AssetType.PHOTO)
    assert platforms.validate_dimensions(spec, 720, 720) == []


def test_gbp_warns_on_a_non_square_photo():
    spec = platforms.resolve(Platform.GOOGLE_BUSINESS_PROFILE, AssetType.PHOTO)
    warnings = platforms.validate_dimensions(spec, 1200, 800)
    assert any("is not a shape" in w for w in warnings)


def test_gbp_still_warns_below_the_minimum():
    spec = platforms.resolve(Platform.GOOGLE_BUSINESS_PROFILE, AssetType.PHOTO)
    warnings = platforms.validate_dimensions(spec, 600, 600)
    assert any("minimum" in w for w in warnings)


def test_larger_square_is_accepted_where_the_spec_states_a_minimum():
    """PMax square is '1200x1200 minimum', so a bigger square is valid."""
    spec = platforms.resolve(Platform.GOOGLE_ADS_PMAX, AssetType.SQUARE)
    assert platforms.validate_dimensions(spec, 1500, 1500) == []


def test_website_hero_accepts_both_published_sizes():
    spec = platforms.resolve(Platform.WEBSITE, AssetType.HERO)
    assert platforms.validate_dimensions(spec, 1920, 1080) == []
    assert platforms.validate_dimensions(spec, 1920, 600) == []


def test_website_section_only_constrains_width():
    spec = platforms.resolve(Platform.WEBSITE, AssetType.SECTION)
    assert platforms.validate_dimensions(spec, 1600, 900) == []
    assert any(
        "minimum" in w for w in platforms.validate_dimensions(spec, 1000, 700)
    )


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
