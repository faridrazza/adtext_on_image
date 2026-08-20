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
        default="",
        description=(
            "The fragment of source_text that makes the headline true. Empty "
            "when the caller supplied the words themselves."
        ),
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


class CopyOption(BaseModel):
    """One candidate copy for the asset, for a person to choose between."""

    headline: str
    subheadline: str | None = Field(
        default=None,
        description=(
            "Supporting line, or null. The copywriter returns one only where "
            "it adds something the headline cannot carry, so many options "
            "legitimately have none."
        ),
    )
    placement: str = Field(
        description=(
            "Region of the photograph this option was written for. Send it "
            "back with the render request to keep the layout it was judged "
            "against."
        )
    )
    source_support: str = Field(
        description="The fragment of source_text that makes this headline true."
    )


class CopyOptionsResponse(BaseModel):
    """Stage 1 on its own: the words, with no image rendered.

    Deliberately narrow. This call decides what the ad should say; the size it
    will be published at, the file it will become and the slot it belongs to
    are all facts about the render, and the render response carries them.
    """

    options: list[CopyOption] = Field(
        description=(
            "Distinct copy options, best first. A person picks one, edits it, "
            "or writes their own, and sends the result to the render endpoint."
        )
    )
    copy_model: str
    headline_word_budget: int = Field(
        description="Headline word limit for this slot, for a UI counter."
    )
    support_word_budget: int = Field(
        description=(
            "Supporting-line word limit for this slot. Zero means the slot has "
            "no room for one, so the field should be hidden."
        )
    )
    alt_text: str | None = None
    warnings: list[str] = Field(default_factory=list)


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
    copy_source: str = Field(
        default="model",
        description=(
            "'model' when the copywriter wrote the words, 'caller' when they "
            "were supplied on the request."
        ),
    )
    font_family: str | None = Field(
        default=None,
        description=(
            "The brand-kit typeface requested for this render, echoed "
            "back. Null when none was sent and the model chose it."
        ),
    )
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
    headline_word_budget: int
    support_word_budget: int


class PlatformInfo(BaseModel):
    platform: str
    asset_types: list[AssetTypeInfo]


class CapabilitiesResponse(BaseModel):
    platforms: list[PlatformInfo]
    model: str
