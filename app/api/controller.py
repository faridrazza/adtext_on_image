"""Main flow for setting ad text over an uploaded image.

This is the only place the end-to-end sequence is expressed. Each step is
delegated to a service; the controller owns ordering, validation and assembly
of the response.
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass

from app.api.schemas import (
    MEDIA_TYPES,
    ApprovedCopy,
    AssetInfo,
    CopyOption,
    CopyOptionsResponse,
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
from app.services.copy_service import CopyService, Placement
from app.services.openai_image_service import OpenAIImageService

logger = logging.getLogger(__name__)

# API-level bounds on requested output dimensions, independent of platform.
MIN_DIMENSION = 64
MAX_DIMENSION = 3840


@dataclass(frozen=True)
class _Prepared:
    """Everything both endpoints need before an AI call is made.

    The copy-options request and the render request validate the same things
    in the same order. Sharing one path is what stops them disagreeing about,
    say, a word budget -- a disagreement the caller would only discover after
    choosing a headline the render slot then refuses.
    """

    spec: object
    width: int
    height: int
    dimension_source: str
    source: object
    image_png: bytes
    font_family: str | None
    warnings: list[str]


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

    def _prepare(
        self,
        *,
        upload: bytes,
        source_text: str | None,
        platform: Platform,
        asset_type: AssetType,
        width: int | None,
        height: int | None,
        font_family: str | None = None,
        require_brief: bool = True,
    ) -> _Prepared:
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

        # 4. Source text is the only permitted basis for *generated* copy, so
        #    it is checked before anything expensive happens -- unchanged for
        #    every request that generates copy, including the exact error a
        #    blank or thin brief has always produced.
        #
        #    When the caller supplies the words, nothing is generated from the
        #    brief. It is then used only to quote alt text from, so its length
        #    is not this service's business and no check applies.
        warnings: list[str] = (
            list(prompt_service.assess_source_text(source_text or ""))
            if require_brief
            else []
        )

        # 5. Decode and measure the upload.
        source = image_service.decode(upload)

        # 6. Output dimensions vs. the platform spec (advisory only). Skipped
        #    when the size came from the spec itself and cannot conflict.
        if dimension_source == "request":
            warnings.extend(platforms.validate_dimensions(spec, width, height))
        warnings.extend(spec.notes)

        return _Prepared(
            spec=spec,
            width=width,
            height=height,
            dimension_source=dimension_source,
            source=source,
            image_png=image_service.to_png_bytes(source.data),
            font_family=font_family,
            warnings=warnings,
        )

    async def write_copy_options(
        self,
        *,
        upload: bytes,
        source_text: str,
        platform: Platform,
        asset_type: AssetType,
        width: int | None = None,
        height: int | None = None,
        count: int = prompt_service.COPY_OPTION_COUNT,
    ) -> CopyOptionsResponse:
        """Stage 1 alone: several sets of words, with no image rendered.

        Nothing is drawn here, so this costs one text call instead of a text
        call plus an image call. The caller picks an option, edits it, or
        writes their own, and sends the result to :meth:`render`.
        """
        prepared = self._prepare(
            upload=upload,
            source_text=source_text,
            platform=platform,
            asset_type=asset_type,
            width=width,
            height=height,
        )
        spec = prepared.spec

        chosen = await self._copy_model.write_options(
            image_png=prepared.image_png,
            source_text=source_text,
            spec=spec,
            width=prepared.width,
            height=prepared.height,
            count=count,
        )

        warnings = list(prepared.warnings)
        if len(chosen.options) < count:
            warnings.append(
                f"{len(chosen.options)} of {count} options are offered; the "
                "rest were dropped for breaking the length or accuracy rules."
            )

        headline_words, support_words = prompt_service.word_budget(spec)

        logger.info(
            "Wrote %d copy options for %s/%s",
            len(chosen.options),
            platform.value,
            asset_type.value,
        )

        return CopyOptionsResponse(
            options=[
                CopyOption(
                    headline=option.headline,
                    subheadline=option.subheadline,
                    placement=option.placement.value,
                    source_support=option.source_support,
                )
                for option in chosen.options
            ],
            copy_model=self._copy_model.model,
            headline_word_budget=headline_words,
            support_word_budget=support_words,
            alt_text=prompt_service.derive_alt_text(source_text, spec),
            warnings=warnings,
        )

    async def render(
        self,
        *,
        upload: bytes,
        source_text: str | None = None,
        platform: Platform,
        asset_type: AssetType,
        width: int | None = None,
        height: int | None = None,
        quality: ImageQuality | None = None,
        font_family: str | None = None,
        headline: str | None = None,
        subheadline: str | None = None,
        placement: Placement | None = None,
    ) -> RenderResponse:
        """Set ad text over the image.

        Send ``headline`` to render words a person chose -- from
        :meth:`write_copy_options`, edited, or written from scratch. The copy
        model is then skipped and those words are rendered as they arrive, and
        ``source_text`` is not needed: nothing is being written, and the image
        model never sees the brief in either case.

        Omit ``headline`` and the copy model decides, exactly as it always has
        -- which does need ``source_text``, because that is the only thing it
        may draw facts from.
        """
        # A presence check only -- the headline is validated further down, but
        # whether one was sent decides if the brief is load-bearing here.
        caller_wrote_the_words = bool(headline and headline.strip())
        # The route requires a headline. Reaching here with a blank one means
        # the field was sent empty, which is a mistake worth naming rather than
        # quietly falling through to the copywriter.
        if headline is not None and not headline.strip():
            raise InvalidRequestError(
                "headline was sent but is empty. Send the words to set.",
            )

        # 1-6. Slot, size, upload limits, source text and the decoded image.
        #      Shared with write_copy_options so the two cannot disagree.
        prepared = self._prepare(
            upload=upload,
            source_text=source_text,
            platform=platform,
            asset_type=asset_type,
            width=width,
            height=height,
            font_family=font_family,
            require_brief=not caller_wrote_the_words,
        )
        spec = prepared.spec
        width, height = prepared.width, prepared.height
        font_family = prepared.font_family
        warnings: list[str] = list(prepared.warnings)

        # 6b. Words the caller chose themselves. Sanitised, not policy-checked:
        #     a person who typed and approved a headline is its author, so the
        #     accuracy and call-to-action rules that police the model do not
        #     apply. What is enforced is structure, because these words are
        #     interpolated into the image prompt.
        headline = prompt_service.clean_user_copy(
            headline,
            field="headline",
            max_chars=prompt_service.MAX_USER_HEADLINE_CHARS,
            max_words=prompt_service.MAX_USER_HEADLINE_WORDS,
        )
        brief = source_text.strip() if source_text else ""
        subheadline = prompt_service.clean_user_copy(
            subheadline,
            field="subheadline",
            max_chars=prompt_service.MAX_USER_SUPPORT_CHARS,
            max_words=prompt_service.MAX_USER_SUPPORT_WORDS,
        )

        # Checked after cleaning, not before: a headline of only whitespace is
        # the same as no headline, and pairing it with a supporting line would
        # otherwise silently discard that line.
        if subheadline is not None and headline is None:
            raise InvalidRequestError(
                "subheadline was sent without headline. Send both, or neither "
                "and let the copy model write them.",
                details={"subheadline": subheadline[:120]},
            )
        if placement is not None and headline is None:
            raise InvalidRequestError(
                "placement was sent without headline. It applies only to words "
                "you supply; the copy model chooses its own.",
                details={"placement": placement.value},
            )

        # 7. Decide the copy -- unless the caller already has. The copy model
        #    reads the photograph and the source text; its output is
        #    policy-checked before anything renders.
        if headline is not None:
            copy_source = "caller"
            source_support = ""
            # No placement sent means the caller wrote their own words and has
            # no region in mind. PLACEMENT_AUTO is not a phrasing the render
            # prompt knows, so it falls through to "over a calm area of the
            # image" and the image model finds the clear space itself.
            placement_value = (
                placement.value if placement else prompt_service.PLACEMENT_AUTO
            )
        else:
            copy_source = "model"
            copy = await self._copy_model.write(
                image_png=prepared.image_png,
                source_text=brief,
                spec=spec,
                width=width,
                height=height,
            )
            headline = copy.headline
            subheadline = copy.subheadline
            placement_value = copy.placement.value
            source_support = copy.source_support

        # 8. Render the approved words. The image model is given only those
        #    words -- never the source text, which it would otherwise transcribe.
        #    This prompt is identical whether a model or a person chose them.
        rendered = await self._image_model.render(
            image_png=prepared.image_png,
            prompt=prompt_service.build_render_prompt(
                headline=headline,
                subheadline=subheadline,
                placement=placement_value,
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

        # 10. Alt text is quoted from the brief, never invented, so there is
        #     none without one. No warning: alt text belongs to the copy step,
        #     where the brief is always present and /copy-options returns it.
        #     This field stays for the single-call flow, which does send a
        #     brief and has always had its alt text from here.
        alt_text = prompt_service.derive_alt_text(brief, spec) if brief else None

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
                width=prepared.source.width,
                height=prepared.source.height,
                image_format=prepared.source.image_format,
                size_bytes=prepared.source.size_bytes,
            ),
            asset=AssetInfo(
                platform=platform.value,
                asset_type=asset_type.value,
                label=spec.label,
                output_width=width,
                output_height=height,
                dimension_source=prepared.dimension_source,
            ),
            ad_copy=ApprovedCopy(
                headline=headline,
                subheadline=subheadline,
                placement=placement_value,
                source_support=source_support,
            ),
            model=rendered.model,
            copy_model=self._copy_model.model,
            quality=rendered.quality,
            copy_source=copy_source,
            font_family=font_family,
            alt_text=alt_text,
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
