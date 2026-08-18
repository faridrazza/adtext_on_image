# Ad Text Image Generator

Give it a photo and some text about a business. It writes a short headline and
sets that headline over the photo, sized correctly for wherever the ad will run.

**It only adds words.** No buttons, logos, icons, badges, shapes or borders.

---

## How it works

The job is split into two steps, because one AI call cannot do both well.

```
   your photo  +  your text  +  platform & asset type
                        │
        STEP 1 ─────────▼──────────────────────────────
        A text AI looks at the photo and reads your text,
        then writes a short headline (max 5-9 words).

        The backend then checks that headline:
          · is it short enough?
          · is it a call-to-action?      → rejected
          · does it contain a number or price
            that is NOT in your text?    → rejected

        STEP 2 ─────────▼──────────────────────────────
        An image AI sets ONLY that approved headline
        onto the photo. It never sees your original
        text, so it cannot copy it onto the image.

        STEP 3 ─────────▼──────────────────────────────
        Pillow resizes the result to the exact pixel
        size and file format the platform requires.
                        │
                        ▼
        JSON: the image + the headline it wrote + warnings
```

**Why the split matters.** When the original text was sent to the image AI, it
pasted the whole paragraph onto the picture. An image AI treats any text in its
prompt as *"draw this"*. Now it only ever receives the final six words.

---

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env
```

Open `.env` and set `OPENAI_API_KEY`. Then start it:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

| What | Where |
|---|---|
| Demo page | http://127.0.0.1:8000/ |
| API docs (try it in the browser) | http://127.0.0.1:8000/docs |

---

## The endpoint

```
POST http://127.0.0.1:8000/api/v1/ad-images/render
Body type: form-data
```

| Field | Required | What to send |
|---|---|---|
| `image` | **yes** | The photo file — JPEG, PNG or WebP |
| `source_text` | **yes** | Your text about the business. Minimum 5 words |
| `platform` | **yes** | `google_ads_pmax` · `meta` · `google_business_profile` · `website` |
| `asset_type` | **yes** | The slot — see the table below |
| `width` | no | Custom width. Only if you want to override the platform size |
| `height` | no | Custom height. Must be sent together with `width` |
| `quality` | no | `low` (default) · `medium` · `high` · `auto` |

You do **not** need to send the size. The backend already knows that
`meta` + `feed_square` means 1080×1080.

### Using Postman

1. Method **POST**, URL `http://127.0.0.1:8000/api/v1/ad-images/render`
2. Go to the **Body** tab, choose **form-data**
3. Add the rows below. For the `image` row, hover the key field and switch its
   type from *Text* to **File**, then pick your photo.

   | Key | Type | Value |
   |---|---|---|
   | `image` | **File** | your photo |
   | `source_text` | Text | Give your space a fresh new look. Professional painting and clean finishes. |
   | `platform` | Text | `meta` |
   | `asset_type` | Text | `feed_square` |

4. Send. Do **not** set a `Content-Type` header yourself — Postman adds the
   correct one automatically.

Expect it to take a while: image generation can run up to about two minutes.

### Same thing in curl

```bash
curl -X POST http://127.0.0.1:8000/api/v1/ad-images/render \
  -F "image=@photo.jpg" \
  -F "source_text=Give your space a fresh new look. Professional painting and clean finishes." \
  -F "platform=meta" \
  -F "asset_type=feed_square"
```

---

## What comes back

```json
{
  "image": {
    "b64": "iVBORw0KGgo...",
    "media_type": "image/jpeg",
    "image_format": "JPEG",
    "width": 1080,
    "height": 1080,
    "size_bytes": 412093
  },
  "ad_copy": {
    "headline": "Fresh Colour, Flawless Finish",
    "subheadline": null,
    "placement": "bottom_left",
    "source_support": "Professional painting, clean finishes"
  },
  "source_image": { "width": 900, "height": 900, "image_format": "PNG", "size_bytes": 98211 },
  "asset": {
    "platform": "meta", "asset_type": "feed_square",
    "label": "Feed square (1:1)",
    "output_width": 1080, "output_height": 1080,
    "dimension_source": "platform_default"
  },
  "model": "gpt-image-2",
  "copy_model": "gpt-5.6",
  "quality": "low",
  "alt_text": null,
  "warnings": ["..."],
  "rendering_notice": "..."
}
```

`image.b64` is the finished picture, base64 encoded. To view it in a browser,
paste `data:image/jpeg;base64,` followed by the string into the address bar.

`ad_copy` is the important one: it tells you exactly what was written on the
image and which part of your text justifies it — so you can check the claim
without having to read the picture.

`warnings` never stops the request. It reports things worth knowing, such as
your chosen size being below a platform minimum.

---

## Platforms and sizes

| Platform | Asset type | Output size |
|---|---|---|
| `google_ads_pmax` | `square` | 1200×1200 |
| | `landscape` | 1200×628 |
| | `portrait` | 1200×1500 |
| | `logo_square`, `logo_wide` | **refused** — logos never get ad text |
| `meta` | `feed_square` | 1080×1080 |
| | `feed_portrait` | 1080×1350 |
| | `story_reel` | 1080×1920 |
| | `facebook_landscape` | 1200×630 |
| `google_business_profile` | `photo` | 1080×1080 |
| `website` | `hero` | 1920×1080 |
| | `section` | 1200×800 |
| | `sidebar_card` | 300×250 |

`GET /api/v1/capabilities` returns this same list as JSON, including allowed
formats and file-size limits, so a front-end never has to hardcode sizes.

---

## Errors

Every error looks the same:

```json
{ "code": "insufficient_source_text", "message": "...", "details": {} }
```

| Code | Status | Meaning |
|---|---|---|
| `invalid_request` | 422 | Sent only one of width/height, or a size out of range |
| `invalid_image` | 422 | File missing, unreadable, wrong type, or too big |
| `unsupported_asset` | 422 | Asset type not valid for that platform, or a logo slot |
| `insufficient_source_text` | 422 | Fewer than 5 words — not enough to write from |
| `copy_generation_failed` | 502 | The headline broke the accuracy rules twice |
| `rendering_failed` | 502 | The image model failed |
| `configuration_error` | 500 | `OPENAI_API_KEY` is not set |

The 422s all happen **before** any AI call, so a bad request costs nothing.

---

## The accuracy rules

Your `source_text` is the only allowed source of facts. The wording is free —
the headline is meant to be rewritten, not copied — but the *claims* are locked.

Three things enforce this:

1. **Before any AI call** — text under 5 words is rejected outright.
2. **The copywriter brief** — bans prices, offers, deadlines, statistics,
   ratings, awards, guarantees, superlatives and any brand name or phone number
   that is not in your text.
3. **A code check on the result** — the backend re-reads the headline and
   rejects it if it contains a number, percentage or price that does not appear
   in your text, or if it reads as a call-to-action. It retries once, then
   fails the request rather than shipping a false claim.

### One limitation, stated plainly

The picture is redrawn by an image AI, not stamped by a graphics library. The
prompt tells it to leave the photo untouched, but pixels are regenerated, so an
exact match with your original cannot be guaranteed. Every response says this in
`rendering_notice`. Check the result before publishing.

---

## Tests

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest
```

144 tests. Both AI calls are faked, so the suite is free to run and needs no
API key.

---

## Project layout

```
app/
├── main.py                       starts the app
├── api/
│   ├── router.py                 the URLs
│   ├── controller.py             the main flow, start to finish
│   └── schemas.py                request and response shapes
├── core/                         settings and error types
├── domain/
│   └── platforms.py              every platform size and rule
└── services/
    ├── copy_service.py           step 1 — writes and checks the headline
    ├── openai_image_service.py   step 2 — renders it
    ├── image_service.py          step 3 — resize, format, file size
    └── prompt_service.py         the two prompts
```

To add a platform, edit `app/domain/platforms.py` only. Sizes, formats, limits
and the demo page all update from there.
