"""Response models for the ad-image API."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class ImageQuality(str, Enum):
    """Render quality accepted by the image model.

    Defaults to LOW: it is markedly cheaper and faster, and text-only overlays
    rarely benefit from the higher tiers.
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    AUTO = "auto"

MEDIA_TYPES = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}

# Stated on every response. The image is produced by a generative model, so the
# output is a re-rendered image rather than the original bytes with an overlay.
RENDERING_NOTICE = (
    "The output image was produced by a generative image model. The model is "
    "instructed to reproduce the source image unchanged and to add text only, "
    "but pixels are re-rendered and exact preservation of the original is not "
    "guaranteed. Review before publishing."
)


class ApprovedCopy(BaseModel):
    """The words actually set on the image, and what justifies them."""

    headline: str
    subheadline: str | None = None
    placement: str
    source_support: str = Field(
        description="The fragment of source_text that makes the headline true."
    )


class RenderedImage(BaseModel):
    b64: str = Field(description="Base64-encoded image bytes.")
    media_type: str
    image_format: str
    width: int
    height: int
    size_bytes: int


class SourceImageInfo(BaseModel):
    width: int
    height: int
    image_format: str
    size_bytes: int


class AssetInfo(BaseModel):
    platform: str
    asset_type: str
    label: str
    output_width: int
    output_height: int
    dimension_source: str = Field(
        description=(
            "'platform_default' when the size came from the platform spec, "
            "'request' when the caller supplied width and height."
        )
    )


class RenderResponse(BaseModel):
    image: RenderedImage
    # Not named `copy`: that shadows BaseModel.copy on pydantic models.
    ad_copy: ApprovedCopy = Field(
        description=(
            "The copy written for this asset. Returned so every claim on the "
            "image can be audited without reading pixels."
        )
    )
    source_image: SourceImageInfo
    asset: AssetInfo
    model: str
    copy_model: str
    quality: str = Field(description="Render quality actually used.")
    alt_text: str | None = Field(
        default=None,
        description=(
            "Derived verbatim from source_text for platforms that require alt "
            "text. Never invented; null when the platform does not require it."
        ),
    )
    warnings: list[str] = Field(default_factory=list)
    rendering_notice: str = RENDERING_NOTICE


class ErrorResponse(BaseModel):
    code: str
    message: str
    details: dict = Field(default_factory=dict)


class AssetTypeInfo(BaseModel):
    """Everything a client needs to drive the form without hardcoding sizes."""

    asset_type: str
    label: str
    default_width: int
    default_height: int
    published_sizes: list[str]
    min_width: int | None = None
    min_height: int | None = None
    allowed_formats: list[str]
    min_bytes: int | None = None
    max_bytes: int | None = None
    accepts_text_overlay: bool
    requires_alt_text: bool


class PlatformInfo(BaseModel):
    platform: str
    asset_types: list[AssetTypeInfo]


class CapabilitiesResponse(BaseModel):
    platforms: list[PlatformInfo]
    model: str
