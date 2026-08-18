"""Domain errors.

Every failure the API can produce deliberately maps to one of these, so the
HTTP layer never has to interpret arbitrary exceptions.
"""


class AdImageError(Exception):
    """Base class for expected, user-facing failures."""

    status_code = 400
    code = "ad_image_error"

    def __init__(self, message: str, *, details: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class InvalidRequestError(AdImageError):
    """Request fields are individually valid but inconsistent with each other."""

    status_code = 422
    code = "invalid_request"


class InvalidImageError(AdImageError):
    """Upload is missing, unreadable, or not a supported image."""

    status_code = 422
    code = "invalid_image"


class UnsupportedAssetError(AdImageError):
    """Requested platform/asset_type combination is not renderable."""

    status_code = 422
    code = "unsupported_asset"


class InsufficientSourceTextError(AdImageError):
    """Source text carries too little substance to build honest ad copy."""

    status_code = 422
    code = "insufficient_source_text"


class RenderingError(AdImageError):
    """The image model failed or returned something unusable."""

    status_code = 502
    code = "rendering_failed"


class ConfigurationError(AdImageError):
    """The service is missing configuration it needs to run."""

    status_code = 500
    code = "configuration_error"
