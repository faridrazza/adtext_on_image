"""Main flow for setting ad text over an uploaded image.

This is the only place the end-to-end sequence is expressed. Each step is
delegated to a service; the controller owns ordering, validation and assembly
of the response.
"""

from __future__ import annotations

import base64
import logging

from app.api.schemas import (
    MEDIA_TYPES,
    ApprovedCopy,
    AssetInfo,
    ImageQuality,
    RenderedImage,
    RenderResponse,
    SourceImageInfo,
)
from app.core.config import Settings
from app.core.errors import (
    InvalidImageError,
    InvalidRequestError,
    UnsupportedAssetError,
)
from app.domain import platforms
from app.domain.platforms import AssetType, Platform
from app.services import image_service, prompt_service
from app.services.copy_service import CopyService
from app.services.openai_image_service import OpenAIImageService

logger = logging.getLogger(__name__)

# API-level bounds on requested output dimensions, independent of platform.
MIN_DIMENSION = 64
MAX_DIMENSION = 3840


class AdImageController:
    def __init__(
        self,
        image_model: OpenAIImageService,
        copy_model: CopyService,
        settings: Settings,
    ) -> None:
        self._image_model = image_model
        self._copy_model = copy_model
        self._settings = settings

    async def render(
        self,
        *,
        upload: bytes,
        source_text: str,
        platform: Platform,
        asset_type: AssetType,
        width: int | None = None,
        height: int | None = None,
        quality: ImageQuality | None = None,
        font_family: str | None = None,
    ) -> RenderResponse:
        # 1. Resolve the target slot. Raises if the combination is invalid.
        spec = platforms.resolve(platform, asset_type)
        if not spec.allows_text_overlay:
            raise UnsupportedAssetError(
                f"'{spec.label}' is a brand asset and must not receive generated "
                "ad copy. Supply a marketing image asset type instead.",
                details={"asset_type": asset_type.value},
            )

        # 2. Output size comes from the platform spec unless the caller
        #    deliberately overrides it.
        width, height, dimension_source = self._resolve_dimensions(
            spec, width, height
        )

        # 3. Reject oversized uploads before spending time decoding them.
        if len(upload) > self._settings.max_upload_bytes:
            raise InvalidImageError(
                f"The uploaded file is {len(upload)} bytes, above the "
                f"{self._settings.max_upload_bytes} byte limit.",
                details={"max_upload_bytes": self._settings.max_upload_bytes},
            )

        # 3b. A brand-kit typeface, if one was sent. Checked here because
        #     it is interpolated into the render prompt.
        font_family = prompt_service.clean_font_family(font_family)

        # 4. Source text is the only permitted basis for the copy, so it is
        #    checked before anything expensive happens.
        warnings: list[str] = list(prompt_service.assess_source_text(source_text))

        # 5. Decode and measure the upload.
        source = image_service.decode(upload)

        # 6. Output dimensions vs. the platform spec (advisory only). Skipped
        #    when the size came from the spec itself and cannot conflict.
        if dimension_source == "request":
            warnings.extend(platforms.validate_dimensions(spec, width, height))
        warnings.extend(spec.notes)

        # 7. Decide the copy. The copy model reads the photograph and the
        #    source text; its output is policy-checked before anything renders.
        image_png = image_service.to_png_bytes(source.data)
        copy = await self._copy_model.write(
            image_png=image_png,
            source_text=source_text,
            spec=spec,
            width=width,
            height=height,
        )

        # 8. Render the approved words. The image model is given only those
        #    words -- never the source text, which it would otherwise transcribe.
        rendered = await self._image_model.render(
            image_png=image_png,
            prompt=prompt_service.build_render_prompt(
                headline=copy.headline,
                subheadline=copy.subheadline,
                placement=copy.placement.value,
                spec=spec,
                width=width,
                height=height,
                font_family=font_family,
            ),
            width=width,
            height=height,
            quality=quality.value if quality else None,
        )
        warnings.extend(rendered.size_plan.warnings)

        # 9. Force the result to the exact target size, format and weight.
        final, encode_warnings = image_service.finalize(
            rendered.image_bytes, width=width, height=height, spec=spec
        )
        warnings.extend(encode_warnings)

        logger.info(
            "Rendered %s/%s at %dx%d (%d bytes, %d warnings)",
            platform.value,
            asset_type.value,
            width,
            height,
            final.size_bytes,
            len(warnings),
        )

        return RenderResponse(
            image=RenderedImage(
                b64=base64.b64encode(final.data).decode("ascii"),
                media_type=MEDIA_TYPES[final.image_format],
                image_format=final.image_format,
                width=final.width,
                height=final.height,
                size_bytes=final.size_bytes,
            ),
            source_image=SourceImageInfo(
                width=source.width,
                height=source.height,
                image_format=source.image_format,
                size_bytes=source.size_bytes,
            ),
            asset=AssetInfo(
                platform=platform.value,
                asset_type=asset_type.value,
                label=spec.label,
                output_width=width,
                output_height=height,
                dimension_source=dimension_source,
            ),
            ad_copy=ApprovedCopy(
                headline=copy.headline,
                subheadline=copy.subheadline,
                placement=copy.placement.value,
                source_support=copy.source_support,
            ),
            model=rendered.model,
            copy_model=self._copy_model.model,
            quality=rendered.quality,
            font_family=font_family,
            alt_text=prompt_service.derive_alt_text(source_text, spec),
            warnings=warnings,
        )

    @staticmethod
    def _resolve_dimensions(
        spec, width: int | None, height: int | None
    ) -> tuple[int, int, str]:
        """Derive the output size from the platform spec, or honour an override.

        The platform and asset type already determine the published size, so the
        caller only needs to send dimensions when deliberately going off-spec.
        """
        if width is None and height is None:
            default_width, default_height = spec.default_size
            return default_width, default_height, "platform_default"

        if width is None or height is None:
            raise InvalidRequestError(
                "Provide both width and height to override the platform default, "
                "or neither to use it.",
                details={"width": width, "height": height},
            )

        if not (MIN_DIMENSION <= width <= MAX_DIMENSION) or not (
            MIN_DIMENSION <= height <= MAX_DIMENSION
        ):
            raise InvalidRequestError(
                f"Dimensions must be between {MIN_DIMENSION} and "
                f"{MAX_DIMENSION} pixels.",
                details={"width": width, "height": height},
            )

        return width, height, "request"
