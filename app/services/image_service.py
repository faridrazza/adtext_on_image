"""Deterministic image handling: decode, inspect, normalize, encode.

All Pillow work lives here. Nothing in this module talks to the model -- it is
pure, synchronous and unit-testable.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

from PIL import Image, ImageCms, UnidentifiedImageError

from app.core.errors import InvalidImageError
from app.domain.platforms import AssetSpec

# Formats we are willing to decode, mapped to the canonical name used in specs.
DECODABLE_FORMATS = {"JPEG", "PNG", "WEBP"}

# JPEG quality ladder used when squeezing an asset under a max_bytes ceiling.
_JPEG_QUALITY_STEPS = (95, 90, 85, 80, 75, 70, 65, 60)

WEB_DPI = (72, 72)


@dataclass(frozen=True)
class ImageAsset:
    """A decoded image plus the facts the rest of the pipeline needs."""

    data: bytes
    width: int
    height: int
    image_format: str
    has_alpha: bool

    @property
    def size_bytes(self) -> int:
        return len(self.data)

    @property
    def dimensions(self) -> tuple[int, int]:
        return self.width, self.height


def decode(data: bytes) -> ImageAsset:
    """Validate and measure an uploaded image.

    Raises InvalidImageError for anything we cannot safely work with.
    """
    if not data:
        raise InvalidImageError("The uploaded file is empty.")

    try:
        with Image.open(io.BytesIO(data)) as img:
            img_format = (img.format or "").upper()
            if img_format not in DECODABLE_FORMATS:
                raise InvalidImageError(
                    f"Unsupported image format '{img_format or 'unknown'}'. "
                    "Supported formats are JPEG, PNG and WebP.",
                    details={"detected_format": img_format or None},
                )
            width, height = img.size
            has_alpha = img.mode in ("RGBA", "LA", "PA") or "transparency" in img.info
            # Force a full decode so truncated or corrupt payloads fail here
            # rather than deeper in the pipeline.
            img.load()
    except InvalidImageError:
        raise
    except Image.DecompressionBombError as exc:
        raise InvalidImageError(
            "The uploaded image is too large to process safely."
        ) from exc
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise InvalidImageError(
            "The uploaded file could not be read as an image."
        ) from exc

    if width <= 0 or height <= 0:
        raise InvalidImageError("The uploaded image has invalid dimensions.")

    return ImageAsset(
        data=data,
        width=width,
        height=height,
        image_format=img_format,
        has_alpha=has_alpha,
    )


def to_png_bytes(data: bytes) -> bytes:
    """Re-encode to PNG for upload to the image model.

    The edit endpoint expects a lossless PNG, and this also strips any exotic
    colour space before the image leaves the process.
    """
    with Image.open(io.BytesIO(data)) as img:
        img = _to_srgb(img)
        mode = "RGBA" if img.mode in ("RGBA", "LA", "PA") else "RGB"
        converted = img.convert(mode)
        buffer = io.BytesIO()
        converted.save(buffer, format="PNG", optimize=True)
        return buffer.getvalue()


def choose_output_format(spec: AssetSpec, needs_alpha: bool) -> str:
    """Pick an output format that satisfies the spec.

    Prefers JPEG for photographic assets because it is far smaller, but falls
    back to PNG whenever transparency is required or JPEG is not allowed.
    """
    allowed = spec.allowed_formats
    if spec.requires_transparency or needs_alpha:
        for candidate in ("PNG", "WEBP"):
            if candidate in allowed:
                return candidate
    if "JPEG" in allowed:
        return "JPEG"
    return allowed[0]


def finalize(
    rendered: bytes,
    *,
    width: int,
    height: int,
    spec: AssetSpec,
) -> tuple[ImageAsset, list[str]]:
    """Bring a rendered image to the exact requested size, format and weight.

    Returns the finished asset plus any warnings that could not be resolved
    automatically.
    """
    warnings: list[str] = []

    try:
        with Image.open(io.BytesIO(rendered)) as img:
            img.load()
            source = _to_srgb(img)
            source_size = source.size
            needs_alpha = source.mode in ("RGBA", "LA", "PA")

            if source_size != (width, height):
                warnings.append(
                    f"The image model returned {source_size[0]}x{source_size[1]}; "
                    f"resampled to the requested {width}x{height}."
                )
                source = source.resize((width, height), Image.LANCZOS)

            out_format = choose_output_format(spec, needs_alpha)
            data = _encode(source, out_format, spec, warnings)
    except InvalidImageError:
        raise
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise InvalidImageError(
            "The rendered image could not be processed."
        ) from exc

    return (
        ImageAsset(
            data=data,
            width=width,
            height=height,
            image_format=out_format,
            has_alpha=out_format != "JPEG" and needs_alpha,
        ),
        warnings,
    )


# --------------------------------------------------------------------------
# internals


def _to_srgb(img: Image.Image) -> Image.Image:
    """Convert to sRGB when the image carries a different ICC profile.

    Falls back to the original image if the profile is unreadable -- a bad
    profile should not fail the request.
    """
    profile = img.info.get("icc_profile")
    if not profile:
        return img.copy()
    try:
        src = ImageCms.ImageCmsProfile(io.BytesIO(profile))
        dst = ImageCms.createProfile("sRGB")
        return ImageCms.profileToProfile(img, src, dst, outputMode=img.mode)
    except Exception:  # noqa: BLE001 - never fail a render over a colour profile
        return img.copy()


def _encode(
    img: Image.Image,
    out_format: str,
    spec: AssetSpec,
    warnings: list[str],
) -> bytes:
    """Encode, stepping quality down until the asset fits its size ceiling."""
    if out_format == "JPEG":
        # JPEG cannot carry alpha; composite onto white so transparent regions
        # do not turn black.
        if img.mode in ("RGBA", "LA", "PA"):
            background = Image.new("RGB", img.size, (255, 255, 255))
            background.paste(img, mask=img.convert("RGBA").split()[-1])
            img = background
        else:
            img = img.convert("RGB")

        data = b""
        for quality in _JPEG_QUALITY_STEPS:
            data = _write(img, "JPEG", quality=quality, optimize=True, progressive=True)
            if spec.max_bytes is None or len(data) <= spec.max_bytes:
                break
    elif out_format == "WEBP":
        img = img.convert("RGBA" if img.mode in ("RGBA", "LA", "PA") else "RGB")
        data = _write(img, "WEBP", quality=90, method=6)
    else:  # PNG
        img = img.convert("RGBA" if img.mode in ("RGBA", "LA", "PA") else "RGB")
        data = _write(img, "PNG", optimize=True)

    if spec.max_bytes is not None and len(data) > spec.max_bytes:
        warnings.append(
            f"Output is {len(data)} bytes, above the "
            f"{spec.max_bytes} byte limit for {spec.label}."
        )
    if spec.min_bytes is not None and len(data) < spec.min_bytes:
        warnings.append(
            f"Output is {len(data)} bytes, below the "
            f"{spec.min_bytes} byte minimum for {spec.label}."
        )

    return data


def _write(img: Image.Image, fmt: str, **options) -> bytes:
    buffer = io.BytesIO()
    img.save(buffer, format=fmt, dpi=WEB_DPI, **options)
    return buffer.getvalue()
