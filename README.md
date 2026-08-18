# Ad Text & CTA Image Generator

Adds platform-appropriate ad copy and a call-to-action to an existing image.
The caller supplies the image, the source text, the platform and the asset type;
the backend derives the output size, builds a constrained editing brief, renders
via an OpenAI image model, and normalises the result to the exact target size,
format and file-size envelope.

---

## Request flow

```
POST /api/v1/ad-images/render          (multipart/form-data)
  image · source_text · platform · asset_type · [width] · [height]
        │
   1 ── resolve platform + asset_type ─▶ AssetSpec (sizes, formats, limits)
   2 ── derive output size from the spec (or honour an explicit override)
   3 ── reject oversized uploads
   4 ── check source_text carries enough substance to be honest
   5 ── decode + measure the upload                        (Pillow)
   6 ── compare size against the spec                      → warnings
   7 ── build the editing brief and render                 (OpenAI images.edit)
   8 ── force exact size / format / weight                 (Pillow)
        │
        └─▶ JSON: base64 image + source info + asset info + warnings
```

Each step lives in its own module, so a change to platform rules never touches
the render path and vice versa.

```
app/
├── main.py                          FastAPI app, CORS, error handlers
├── api/
│   ├── router.py                    routes and request shape
│   ├── controller.py                the end-to-end flow (single entry point)
│   ├── dependencies.py              wiring
│   └── schemas.py                   response models
├── core/
│   ├── config.py                    env-driven settings
│   └── errors.py                    typed domain errors → HTTP codes
├── domain/
│   └── platforms.py                 platform + asset spec registry
└── services/
    ├── image_service.py             all Pillow work
    ├── prompt_service.py            prompt + source-text policy
    └── openai_image_service.py      OpenAI adapter + output-size planning
```

---

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env      # then set OPENAI_API_KEY
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

Interactive docs at `http://127.0.0.1:8000/docs`.

Run the tests with `.\.venv\Scripts\python.exe -m pytest` (needs
`requirements-dev.txt`). The suite covers the spec registry, size planning,
prompt policy, image pipeline and API contract; the OpenAI call is stubbed, so
no key and no spend is required.

---

## Output dimensions

`platform` + `asset_type` determine the output size — the caller does not need
to know it. `GET /api/v1/capabilities` returns every slot with its default size,
allowed formats and file-size limits, so a UI can build its form without
hardcoding anything.

| Platform | Asset type | Output |
|---|---|---|
| `google_ads_pmax` | `square` / `landscape` / `portrait` | 1200×1200 · 1200×628 · 1200×1500 |
| `google_ads_pmax` | `logo_square` / `logo_wide` | **rejected** — brand assets never receive generated copy |
| `meta` | `feed_square` / `feed_portrait` / `story_reel` / `facebook_landscape` | 1080×1080 · 1080×1350 · 1080×1920 · 1200×630 |
| `google_business_profile` | `photo` | 1080×1080 (720×720 minimum) |
| `website` | `hero` / `section` / `sidebar_card` | 1920×1080 · 1200×800 · 300×250 |

`width` and `height` are optional overrides and must be sent together. Off-spec
sizes still render; the mismatch comes back in `warnings` rather than failing
the request.

`website/section` publishes only a minimum width, so its 1200×800 default is a
backend choice rather than a platform requirement — it is flagged in `warnings`
and set in `app/domain/platforms.py`.

### Why a resize step exists

`gpt-image-2` accepts arbitrary sizes but requires both edges to be **divisible
by 16**, within its pixel and aspect limits. Meta's sizes are built on 1080,
which is not — so 1080×1080 is rendered at 1088×1088 and downsampled back by
Pillow. Sizes are always rounded **up** so the correction is a downsample, which
stays sharp, rather than an upsample, which softens.

Older models (`gpt-image-1`, `gpt-image-1.5`, `gpt-image-1-mini`) are limited to
three fixed sizes, so every request on those incurs a larger resample. Prefer
`gpt-image-2`.

---

## Accuracy controls

The source text is the only permitted basis for anything written on the image.
Three mechanisms enforce that:

1. **Pre-flight** — `source_text` under 5 words is rejected outright with
   `insufficient_source_text` before any billable call. Thin-but-usable text
   renders with a warning, and produces minimal copy rather than padding.
2. **Grounded CTAs** — the prompt offers only CTAs the source text actually
   supports. "Book Now" appears only when booking language is present; text with
   no commercial signal gets `Learn More` / `Contact Us` and nothing else.
3. **The editing brief** — six explicit rules covering image preservation, the
   source-of-truth constraint, an itemised prohibition list (prices, offers,
   statistics, guarantees, superlatives, logos, contact details), readability,
   platform layout, and what to do when the source text is insufficient. The
   source text is delimited and marked as data, so instructions embedded inside
   it are not followed.

Alt text, where the platform requires it, is taken **verbatim** from the source
text and truncated — never rewritten or invented.

### Known limitation

Rendering is done by a generative image model, so the output is a re-rendered
image rather than the original bytes with an overlay. The prompt instructs the
model to reproduce the source image unchanged, but **exact pixel preservation
cannot be guaranteed or verified**, and the copy exists only as pixels — nothing
downstream can audit what was actually written. Every response carries this as
`rendering_notice`.

If that guarantee is ever needed, the structure already supports it: render onto
a masked region and composite the model's output back over the original with
Pillow, or generate the copy as text and draw it deterministically. Both slot in
behind `OpenAIImageService` without touching the controller, the registry or the
platform rules.

---

## API

### `POST /api/v1/ad-images/render`

| Field | Type | Required | Notes |
|---|---|---|---|
| `image` | file | yes | JPEG, PNG or WebP |
| `source_text` | string | yes | The only permitted source of facts |
| `platform` | enum | yes | See the table above |
| `asset_type` | enum | yes | Must belong to the platform |
| `width` / `height` | int | no | Override the platform default; send both |

```json
{
  "image": { "b64": "...", "media_type": "image/jpeg", "image_format": "JPEG",
             "width": 1080, "height": 1080, "size_bytes": 412093 },
  "source_image": { "width": 800, "height": 800,
                    "image_format": "PNG", "size_bytes": 98211 },
  "asset": { "platform": "meta", "asset_type": "feed_square",
             "label": "Feed square (1:1)", "output_width": 1080,
             "output_height": 1080, "dimension_source": "platform_default" },
  "model": "gpt-image-2",
  "alt_text": null,
  "warnings": ["Requested 1080x1080 is not directly renderable ..."],
  "rendering_notice": "The output image was produced by a generative ..."
}
```

### Errors

Every failure returns `{ "code", "message", "details" }`.

| Code | Status | Cause |
|---|---|---|
| `invalid_request` | 422 | Only one of width/height, or out of range |
| `invalid_image` | 422 | Missing, unreadable, unsupported or oversized upload |
| `unsupported_asset` | 422 | Asset type not valid for the platform, or a logo asset |
| `insufficient_source_text` | 422 | Source text cannot support honest copy |
| `rendering_failed` | 502 | The image model failed or returned nothing usable |
| `configuration_error` | 500 | `OPENAI_API_KEY` not set |

### Other endpoints

- `GET /api/v1/capabilities` — platforms, asset slots, default sizes, limits
- `GET /api/v1/health`

---

## Adding a platform

Add the enum members and a `SPECS` entry in `app/domain/platforms.py`. Sizes,
formats, file-size bounds, layout guidance and validation all flow from there
into the prompt, the size planner, the encoder and `/capabilities`. No other
file changes.
