"""HTTP routes. Request shape in, controller out -- no business logic here."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, UploadFile

from app.api.controller import AdImageController
from app.api.dependencies import get_controller
from app.api.schemas import (
    AssetTypeInfo,
    CapabilitiesResponse,
    ErrorResponse,
    ImageQuality,
    PlatformInfo,
    RenderResponse,
)
from app.core.config import get_settings
from app.domain import platforms
from app.domain.platforms import AssetType, Platform

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
                    )
                    for spec in platforms.specs_for(platform)
                ],
            )
            for platform in Platform
        ],
        model=get_settings().openai_image_model,
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
    controller: AdImageController = Depends(get_controller),
) -> RenderResponse:
    """Set approved ad text over the supplied image. Text only -- no
    call-to-action, logo, icon or graphic is added.

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
    )
