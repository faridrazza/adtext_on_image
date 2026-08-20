"""HTTP routes. Request shape in, controller out -- no business logic here."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, UploadFile

from app.api.controller import AdImageController
from app.api.dependencies import get_controller
from app.api.schemas import (
    AssetTypeInfo,
    CapabilitiesResponse,
    CopyOptionsResponse,
    ErrorResponse,
    ImageQuality,
    PlatformInfo,
    RenderResponse,
)
from app.core.config import get_settings
from app.domain import platforms
from app.domain.platforms import AssetType, Platform
from app.services import prompt_service
from app.services.copy_service import Placement

router = APIRouter(prefix="/api/v1")


@router.get("/health", tags=["meta"])
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/capabilities", response_model=CapabilitiesResponse, tags=["meta"])
async def capabilities() -> CapabilitiesResponse:
    """Supported platforms, their asset slots and each slot's output size.

    Lets a client build the request form without hardcoding platform sizes.
    """
    return CapabilitiesResponse(
        platforms=[
            PlatformInfo(
                platform=platform.value,
                asset_types=[
                    AssetTypeInfo(
                        asset_type=spec.asset_type.value,
                        label=spec.label,
                        default_width=spec.default_size[0],
                        default_height=spec.default_size[1],
                        published_sizes=[
                            f"{w}x{h}" for w, h in spec.exact_sizes
                        ],
                        min_width=spec.min_width,
                        min_height=spec.min_height,
                        allowed_formats=list(spec.allowed_formats),
                        min_bytes=spec.min_bytes,
                        max_bytes=spec.max_bytes,
                        accepts_text_overlay=spec.allows_text_overlay,
                        requires_alt_text=spec.requires_alt_text,
                        headline_word_budget=prompt_service.word_budget(spec)[0],
                        support_word_budget=prompt_service.word_budget(spec)[1],
                    )
                    for spec in platforms.specs_for(platform)
                ],
            )
            for platform in Platform
        ],
        model=get_settings().openai_image_model,
    )


@router.post(
    "/ad-images/copy-options",
    response_model=CopyOptionsResponse,
    tags=["ad-images"],
    responses={
        422: {"model": ErrorResponse},
        502: {"model": ErrorResponse},
    },
)
async def write_copy_options(
    image: UploadFile = File(..., description="Source image (JPEG, PNG or WebP)."),
    source_text: str = Form(
        ...,
        description=(
            "The only permitted source of facts for the generated copy. "
            "Nothing not supported by this text may appear on the image."
        ),
    ),
    platform: Platform = Form(..., description="Target advertising platform."),
    asset_type: AssetType = Form(
        ..., description="Asset slot within the platform."
    ),
    width: int | None = Form(
        None,
        description=(
            "Optional output width, matching the render request so the copy is "
            "written for the size it will be set at. Must be sent with height."
        ),
    ),
    height: int | None = Form(
        None,
        description="Optional output height. Must be sent with width.",
    ),
    controller: AdImageController = Depends(get_controller),
) -> CopyOptionsResponse:
    """Write several copy options for this image, without rendering anything.

    Stage one of a two-step flow: show the options to a person, let them pick
    one, edit it, or write their own, then send the words they settled on to
    ``/ad-images/render``. Each option carries its own placement -- send that
    back too, to keep the layout the words were judged against.

    Costs one text call. No image is generated here.
    """
    return await controller.write_copy_options(
        upload=await image.read(),
        source_text=source_text,
        platform=platform,
        asset_type=asset_type,
        width=width,
        height=height,
    )


@router.post(
    "/ad-images/render",
    response_model=RenderResponse,
    tags=["ad-images"],
    responses={
        422: {"model": ErrorResponse},
        502: {"model": ErrorResponse},
    },
)
async def render_ad_image(
    image: UploadFile = File(..., description="Source image (JPEG, PNG or WebP)."),
    source_text: str | None = Form(
        None,
        description=(
            "The brief the copywriter draws its facts from. Required only when "
            "you do not send a headline. When you do send one there is nothing "
            "to write, so the brief is not needed -- it is also used to quote "
            "alt text from, so send it if the slot requires alt text."
        ),
    ),
    platform: Platform = Form(..., description="Target advertising platform."),
    asset_type: AssetType = Form(
        ..., description="Asset slot within the platform."
    ),
    width: int | None = Form(
        None,
        description=(
            "Optional output width. Omit to use the published size for this "
            "platform and asset type. Must be sent together with height."
        ),
    ),
    height: int | None = Form(
        None,
        description=(
            "Optional output height. Omit to use the published size for this "
            "platform and asset type. Must be sent together with width."
        ),
    ),
    quality: ImageQuality | None = Form(
        None,
        description=(
            "Render quality: low, medium, high or auto. Omit to use the "
            "configured default (low), which is cheapest and fastest."
        ),
    ),
    font_family: str | None = Form(
        None,
        description=(
            "Brand-kit typeface for the rendered text, e.g. Arial. Omit "
            "to let the model choose the typeface, which is the "
            "behaviour when no brand kit applies."
        ),
    ),
    headline: str = Form(
        ...,
        description=(
            "The words to set: an option from /ad-images/copy-options, an edit "
            "of one, or a person's own. Required -- this endpoint renders "
            "words, it does not write them. Ask /ad-images/copy-options for "
            "those first."
        ),
    ),
    subheadline: str | None = Form(
        None,
        description=(
            "Supporting line, set smaller beneath the headline. Optional "
            "because the copywriter returns one only where it adds something "
            "the headline cannot carry -- so an option may legitimately have "
            "none. Send it whenever the option you are rendering has one."
        ),
    ),
    placement: Placement | None = Form(
        None,
        description=(
            "Where the words sit. Send the placement that came with the "
            "option you are rendering. Omit it and the image model finds the "
            "clear space itself."
        ),
    ),
    controller: AdImageController = Depends(get_controller),
) -> RenderResponse:
    """Set the supplied ad text over the supplied image. Text only -- no
    call-to-action, logo, icon or graphic is added.

    This endpoint renders words; it does not write them. ``headline`` is
    required, and ``subheadline`` and ``placement`` come with it -- normally
    straight from an option returned by ``/ad-images/copy-options``, edited or
    replaced by a person first if they wish.

    Output dimensions are derived from ``platform`` and ``asset_type``; supply
    ``width`` and ``height`` only to deliberately override them.
    """
    return await controller.render(
        upload=await image.read(),
        source_text=source_text,
        platform=platform,
        asset_type=asset_type,
        width=width,
        height=height,
        quality=quality,
        font_family=font_family,
        headline=headline,
        subheadline=subheadline,
        placement=placement,
    )
