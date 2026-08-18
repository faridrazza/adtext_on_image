"""Platform and asset-type specifications.

Single source of truth for what each ad slot requires. Everything the rest of
the app needs to know about a platform lives here, so adding a platform is a
matter of adding entries to ``SPECS`` and the two enums -- no service, no
controller, and no prompt code changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

# Requested dimensions rarely land on an exact textbook ratio, so compare with
# a small relative tolerance instead of exact equality.
ASPECT_TOLERANCE = 0.02

KB = 1024
MB = 1024 * 1024


class Platform(str, Enum):
    GOOGLE_ADS_PMAX = "google_ads_pmax"
    META = "meta"
    GOOGLE_BUSINESS_PROFILE = "google_business_profile"
    WEBSITE = "website"


class AssetType(str, Enum):
    # Google Ads PMax
    SQUARE = "square"
    LANDSCAPE = "landscape"
    PORTRAIT = "portrait"
    LOGO_SQUARE = "logo_square"
    LOGO_WIDE = "logo_wide"

    # Meta (Facebook + Instagram)
    FEED_SQUARE = "feed_square"
    FEED_PORTRAIT = "feed_portrait"
    STORY_REEL = "story_reel"
    FACEBOOK_LANDSCAPE = "facebook_landscape"

    # Google Business Profile
    PHOTO = "photo"

    # Website
    HERO = "hero"
    SECTION = "section"
    SIDEBAR_CARD = "sidebar_card"


@dataclass(frozen=True)
class AssetSpec:
    """Everything known about one platform + asset-type slot."""

    platform: Platform
    asset_type: AssetType
    label: str

    #: Logos and brand marks must never receive generated ad copy.
    allows_text_overlay: bool = True

    min_width: int | None = None
    min_height: int | None = None
    #: Exact sizes the platform publishes. First entry is the recommended one.
    exact_sizes: tuple[tuple[int, int], ...] = ()
    #: Allowed width/height ratios, when the platform cares about shape.
    aspect_ratios: tuple[float, ...] = ()

    allowed_formats: tuple[str, ...] = ("JPEG", "PNG")
    min_bytes: int | None = None
    max_bytes: int | None = None

    requires_transparency: bool = False
    requires_alt_text: bool = False

    #: Where text may safely sit. Passed verbatim into the render prompt.
    layout_guidance: str = ""
    #: Operational notes surfaced to the caller as warnings.
    notes: tuple[str, ...] = field(default_factory=tuple)

    #: Only needed where the platform publishes no exact size (it specifies a
    #: minimum instead), so a house default has to stand in.
    default_size_override: tuple[int, int] | None = None

    @property
    def recommended_size(self) -> tuple[int, int] | None:
        return self.exact_sizes[0] if self.exact_sizes else None

    @property
    def default_size(self) -> tuple[int, int]:
        """Output size used when the caller does not specify one."""
        if self.default_size_override is not None:
            return self.default_size_override
        if self.exact_sizes:
            return self.exact_sizes[0]
        raise ValueError(
            f"{self.platform.value}/{self.asset_type.value} has no default size."
        )

    def describe(self) -> str:
        """A compact, human-readable spec line used inside the render prompt."""
        parts = [f"{self.platform.value} / {self.asset_type.value} ({self.label})"]
        if self.exact_sizes:
            sizes = " or ".join(f"{w}x{h}" for w, h in self.exact_sizes)
            parts.append(f"published size: {sizes}")
        if self.min_width or self.min_height:
            parts.append(
                f"minimum: {self.min_width or '-'}x{self.min_height or '-'}"
            )
        parts.append("formats: " + ", ".join(self.allowed_formats))
        return "; ".join(parts)


def _ratio(width: int, height: int) -> float:
    return width / height


SPECS: dict[tuple[Platform, AssetType], AssetSpec] = {
    # ---------------------------------------------------------------- PMax
    (Platform.GOOGLE_ADS_PMAX, AssetType.SQUARE): AssetSpec(
        platform=Platform.GOOGLE_ADS_PMAX,
        asset_type=AssetType.SQUARE,
        label="Square marketing image",
        min_width=1200,
        min_height=1200,
        exact_sizes=((1200, 1200),),
        aspect_ratios=(1.0,),
        allowed_formats=("JPEG", "PNG"),
        min_bytes=50 * KB,
        max_bytes=5 * MB,
        layout_guidance=(
            "Google may crop this asset toward the centre. Keep all text inside "
            "the central 80% of the frame and away from the outer edges."
        ),
        notes=("WebP is not accepted by Google Ads PMax.",),
    ),
    (Platform.GOOGLE_ADS_PMAX, AssetType.LANDSCAPE): AssetSpec(
        platform=Platform.GOOGLE_ADS_PMAX,
        asset_type=AssetType.LANDSCAPE,
        label="Landscape marketing image (1.91:1)",
        min_width=1200,
        min_height=628,
        exact_sizes=((1200, 628),),
        aspect_ratios=(1200 / 628,),
        allowed_formats=("JPEG", "PNG"),
        min_bytes=50 * KB,
        max_bytes=5 * MB,
        layout_guidance=(
            "Wide, shallow frame. Use a single short headline on one or two lines "
            "to avoid vertical crowding."
        ),
        notes=("WebP is not accepted by Google Ads PMax.",),
    ),
    (Platform.GOOGLE_ADS_PMAX, AssetType.PORTRAIT): AssetSpec(
        platform=Platform.GOOGLE_ADS_PMAX,
        asset_type=AssetType.PORTRAIT,
        label="Portrait marketing image (4:5)",
        min_width=1200,
        min_height=1500,
        exact_sizes=((1200, 1500),),
        aspect_ratios=(4 / 5,),
        allowed_formats=("JPEG", "PNG"),
        min_bytes=50 * KB,
        max_bytes=5 * MB,
        layout_guidance=(
            "Tall frame. Set the text in the lower third, leaving the upper area "
            "for the existing subject."
        ),
        notes=("WebP is not accepted by Google Ads PMax.",),
    ),
    (Platform.GOOGLE_ADS_PMAX, AssetType.LOGO_SQUARE): AssetSpec(
        platform=Platform.GOOGLE_ADS_PMAX,
        asset_type=AssetType.LOGO_SQUARE,
        label="Square logo",
        allows_text_overlay=False,
        exact_sizes=((1200, 1200),),
        aspect_ratios=(1.0,),
        allowed_formats=("PNG",),
        requires_transparency=True,
        max_bytes=5 * MB,
    ),
    (Platform.GOOGLE_ADS_PMAX, AssetType.LOGO_WIDE): AssetSpec(
        platform=Platform.GOOGLE_ADS_PMAX,
        asset_type=AssetType.LOGO_WIDE,
        label="Wide logo",
        allows_text_overlay=False,
        exact_sizes=((1200, 300),),
        aspect_ratios=(4.0,),
        allowed_formats=("PNG",),
        requires_transparency=True,
        max_bytes=5 * MB,
    ),
    # ---------------------------------------------------------------- Meta
    (Platform.META, AssetType.FEED_SQUARE): AssetSpec(
        platform=Platform.META,
        asset_type=AssetType.FEED_SQUARE,
        label="Feed square (1:1)",
        exact_sizes=((1080, 1080),),
        aspect_ratios=(1.0,),
        max_bytes=30 * MB,
        layout_guidance=(
            "Feed placement. Keep text well inside the frame; a lower-third band "
            "reads well against most photography."
        ),
    ),
    (Platform.META, AssetType.FEED_PORTRAIT): AssetSpec(
        platform=Platform.META,
        asset_type=AssetType.FEED_PORTRAIT,
        label="Feed portrait (4:5)",
        exact_sizes=((1080, 1350),),
        aspect_ratios=(4 / 5,),
        max_bytes=30 * MB,
        layout_guidance=(
            "Tall feed placement. Place the text in the lower third so it survives "
            "feed cropping."
        ),
    ),
    (Platform.META, AssetType.STORY_REEL): AssetSpec(
        platform=Platform.META,
        asset_type=AssetType.STORY_REEL,
        label="Story / Reel (9:16)",
        exact_sizes=((1080, 1920),),
        aspect_ratios=(9 / 16,),
        max_bytes=30 * MB,
        layout_guidance=(
            "Full-screen vertical. Instagram and Facebook overlay their own UI on "
            "this asset: keep every element clear of the top 14% and the bottom 20% "
            "of the frame."
        ),
    ),
    (Platform.META, AssetType.FACEBOOK_LANDSCAPE): AssetSpec(
        platform=Platform.META,
        asset_type=AssetType.FACEBOOK_LANDSCAPE,
        label="Facebook landscape (1.91:1)",
        exact_sizes=((1200, 630),),
        aspect_ratios=(1200 / 630,),
        max_bytes=30 * MB,
        layout_guidance=(
            "Wide, shallow frame. One short headline; avoid stacking more than "
            "two lines of text."
        ),
    ),
    # ------------------------------------------- Google Business Profile
    (Platform.GOOGLE_BUSINESS_PROFILE, AssetType.PHOTO): AssetSpec(
        platform=Platform.GOOGLE_BUSINESS_PROFILE,
        asset_type=AssetType.PHOTO,
        label="Business profile photo",
        min_width=720,
        min_height=720,
        exact_sizes=((1080, 1080),),
        max_bytes=5 * MB,
        layout_guidance=(
            "Business Profile photos are shown at small sizes and are often "
            "cropped square. Use a short headline at high contrast."
        ),
        notes=(
            "Google Business Profile shows a maximum of 10 photos; plan the set "
            "accordingly.",
        ),
    ),
    # ------------------------------------------------------------- Website
    (Platform.WEBSITE, AssetType.HERO): AssetSpec(
        platform=Platform.WEBSITE,
        asset_type=AssetType.HERO,
        label="Hero banner",
        exact_sizes=((1920, 1080), (1920, 600)),
        allowed_formats=("JPEG", "PNG", "WEBP"),
        requires_alt_text=True,
        layout_guidance=(
            "Wide hero banner. Text usually sits on one side, over a calmer region "
            "of the image rather than across the centre."
        ),
        notes=("Website assets should be 72 DPI and sRGB.",),
    ),
    (Platform.WEBSITE, AssetType.SECTION): AssetSpec(
        platform=Platform.WEBSITE,
        asset_type=AssetType.SECTION,
        label="Section image",
        min_width=1200,
        allowed_formats=("JPEG", "PNG", "WEBP"),
        requires_alt_text=True,
        # The spec gives only a minimum width, so this 3:2 default is a house
        # choice rather than a platform requirement. Override it per request.
        default_size_override=(1200, 800),
        layout_guidance=(
            "In-page section image. Keep the overlay restrained -- a short headline "
            "only, since surrounding page copy carries the detail."
        ),
        notes=(
            "Website assets should be 72 DPI and sRGB.",
            "Section images have no published height; 1200x800 is a backend "
            "default. Pass width and height to choose your own.",
        ),
    ),
    (Platform.WEBSITE, AssetType.SIDEBAR_CARD): AssetSpec(
        platform=Platform.WEBSITE,
        asset_type=AssetType.SIDEBAR_CARD,
        label="Sidebar / card",
        exact_sizes=((300, 250), (300, 600)),
        aspect_ratios=(1.0, 4 / 5),
        allowed_formats=("JPEG", "PNG", "WEBP"),
        requires_alt_text=True,
        layout_guidance=(
            "Very small render size. Use a handful of words at large relative type; "
            "fine detail will not be legible."
        ),
        notes=("Website assets should be 72 DPI and sRGB.",),
    ),
}


def resolve(platform: Platform, asset_type: AssetType) -> AssetSpec:
    """Look up a spec, or raise if the combination is not supported."""
    from app.core.errors import UnsupportedAssetError

    spec = SPECS.get((platform, asset_type))
    if spec is None:
        valid = sorted(a.value for p, a in SPECS if p is platform)
        raise UnsupportedAssetError(
            f"'{asset_type.value}' is not a valid asset type for "
            f"'{platform.value}'.",
            details={"platform": platform.value, "valid_asset_types": valid},
        )
    return spec


def asset_types_for(platform: Platform) -> list[str]:
    return sorted(a.value for p, a in SPECS if p is platform)


def specs_for(platform: Platform) -> list[AssetSpec]:
    """Every asset spec for a platform, ordered by asset-type name."""
    return sorted(
        (spec for (p, _), spec in SPECS.items() if p is platform),
        key=lambda s: s.asset_type.value,
    )


def validate_dimensions(spec: AssetSpec, width: int, height: int) -> list[str]:
    """Check requested output dimensions against the spec.

    Returns warnings rather than raising: an off-spec asset still renders, and
    the caller decides whether to ship it.
    """
    warnings: list[str] = []

    if spec.min_width and width < spec.min_width:
        warnings.append(
            f"Width {width}px is below the {spec.min_width}px minimum for "
            f"{spec.label}."
        )
    if spec.min_height and height < spec.min_height:
        warnings.append(
            f"Height {height}px is below the {spec.min_height}px minimum for "
            f"{spec.label}."
        )

    if spec.exact_sizes and (width, height) not in spec.exact_sizes:
        sizes = " or ".join(f"{w}x{h}" for w, h in spec.exact_sizes)
        warnings.append(
            f"{width}x{height} is not a published size for {spec.label} "
            f"(expected {sizes})."
        )

    if spec.aspect_ratios:
        actual = _ratio(width, height)
        if not any(
            abs(actual - r) <= r * ASPECT_TOLERANCE for r in spec.aspect_ratios
        ):
            expected = ", ".join(f"{r:.3f}" for r in spec.aspect_ratios)
            warnings.append(
                f"Aspect ratio {actual:.3f} does not match {spec.label} "
                f"(expected {expected})."
            )

    return warnings
