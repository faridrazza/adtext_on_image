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
| Health check | http://127.0.0.1:8000/api/v1/health |
| Platforms, sizes and limits as JSON | http://127.0.0.1:8000/api/v1/capabilities |

The demo page is a convenience for local testing only; the API has no dependency
on it. See **Integrating from a real front-end** below for production use.

---

## The endpoint

```
POST http://127.0.0.1:8000/api/v1/ad-images/render
Body type: form-data
```

These seven fields are the entire contract. Anything else you send is ignored.

| Field | Required | Type | What to send |
|---|---|---|---|
| `image` | **yes** | **file part** | The photo — JPEG, PNG or WebP. Max 30 MB (31,457,280 bytes) |
| `source_text` | **yes** | text | Your text about the business. Minimum 5 words |
| `platform` | **yes** | text | `google_ads_pmax` · `meta` · `google_business_profile` · `website` |
| `asset_type` | **yes** | text | The slot — see the table below. Must belong to that platform |
| `width` | no | integer | Custom width, 64–3840. Must be sent **with** `height` |
| `height` | no | integer | Custom height, 64–3840. Must be sent **with** `width` |
| `quality` | no | text | `low` (default) · `medium` · `high` · `auto` |

### Does it accept dimensions?

Yes — `width` and `height`, and they are **optional overrides, not inputs you
have to supply**. The backend already knows `meta` + `feed_square` means
1080×1080, so send neither field and you get the published size.

Three rules, all enforced before any AI call:

- **Both or neither.** Sending only `width` → `422 invalid_request`,
  *"Provide both width and height to override the platform default, or neither
  to use it."*
- **64 to 3840 pixels**, each side. Outside that → `422 invalid_request`,
  *"Dimensions must be between 64 and 3840 pixels."*
- **An override wins over the platform size**, and is only advisory-checked
  against it. Going below a platform minimum does not fail the request — it
  adds a line to `warnings` and renders anyway.

The response tells you which happened: `asset.dimension_source` is
`platform_default` when you sent nothing, `request` when you overrode it.

One thing to expect in `warnings`: `gpt-image-2` only emits sizes that are
multiples of 16, so 1080×1080 is rendered at 1088×1088 and resampled back down
to exactly 1080×1080 by Pillow. The output is always the size you asked for;
the warning is telling you a resample happened.

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

## Integrating from a real front-end

Read this before wiring the endpoint into a production UI. Every statement below
was checked against the running server.

### There is exactly one way to send the image

`multipart/form-data`, with `image` as a **real file part**. That is the only
accepted transport. These all fail with `422`:

| What a front-end might try | Result |
|---|---|
| `multipart/form-data` with a file part | **the only supported way** |
| JSON body with a base64 `image` string | 422 — all four fields report *"Field required"* |
| `image` as a base64 **text** field in the form | 422 — *"Expected UploadFile, received: `<class 'str'>`"* |
| `image` as a `data:image/jpeg;base64,...` URI | 422 — same error |
| An `image_url` field pointing at a remote file | 422 — *"Field required"* on `image`; the field is ignored |

So the browser must hold the actual bytes — a `File` from an `<input>`, or a
`Blob` you fetched yourself. The API will not download an image for you.

### The one mistake that will bite you

**A blob appended without a filename is rejected.** Starlette only treats a
multipart part as a file when the part carries a `filename`; without one it
arrives as a plain string and fails validation:

```js
// 422 - "Expected UploadFile, received: <class 'str'>"
form.append("image", blob);

// correct - the third argument is what makes it a file part
form.append("image", blob, "photo.jpg");
```

With an `<input type="file">` the filename comes along automatically, so this
only hits you when you build the blob yourself — canvas exports, cropped
images, fetched URLs, pasted clipboard data.

### Working browser call

```js
const form = new FormData();
form.append("image", file, file.name || "photo.jpg");
form.append("source_text", text);
form.append("platform", "meta");
form.append("asset_type", "feed_square");
// optional: form.append("width", "1080"); form.append("height", "1080");

const res = await fetch("http://127.0.0.1:8000/api/v1/ad-images/render", {
  method: "POST",
  body: form,            // do NOT set Content-Type yourself - the browser
});                      // adds the multipart boundary

const data = await res.json();
if (!res.ok) { /* see the Errors section - two different shapes */ }
img.src = `data:${data.image.media_type};base64,${data.image.b64}`;
```

### Four things that will surprise you in production

1. **A request takes about 90 seconds** at `quality=low`, and longer above it.
   A measured run returned `HTTP 200` in **1m30s**. Anything sitting between the
   browser and the app — nginx, an ALB, Cloudflare, an API gateway — usually
   times out at 30 or 60 seconds by default and will cut the request off. Raise
   those read timeouts, and give the UI a real progress state rather than a
   spinner that looks hung.
2. **The image comes back as base64 inside the JSON**, not as a binary body. A
   1080x1080 `low` render is about **630 KB of base64** in a ~617 KB response.
   That is fine to display with a `data:` URI, but do not log the response and
   do not keep it in serialised front-end state.
3. **The whole upload lands in memory before the size check runs.** Starlette
   spools a part over 1 MB to a temp file, but the route then calls
   `await image.read()`, which pulls all of it back into a single `bytes`
   object; only after that is the 30 MB ceiling applied. So the limit protects
   the pipeline, not the process. Cap the file size in the UI as well, and set
   a matching client-body limit on your proxy.
4. **CORS is wide open and there is no authentication.** `cors_allow_origins`
   defaults to `*`; a preflight from any origin returns `200` with
   `access-control-allow-origin: *`. Anyone who can reach the URL can spend your
   OpenAI credit. Put it behind your own auth, and set `CORS_ALLOW_ORIGINS` in
   `.env` to your real front-end origin before it ships.

### Things that are safely tolerated

- **Extra form fields are ignored**, not rejected — you can send fields the API
  does not know about without breaking the request.
- **The `Content-Type` of the file part does not matter.** A JPEG sent as
  `application/octet-stream` is accepted; the format is detected from the bytes
  by Pillow, not from the header.
- **A wrong HTTP method** returns a clean `405`, not a crash.

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
your chosen size being below a platform minimum, or the render being resampled
from a multiple-of-16 size back to the exact size you asked for.

`alt_text` is `null` for every platform except `website`, whose slots require
it. When present it is the first sentence of your `source_text`, trimmed to 125
characters — copied verbatim, never invented.

`asset.dimension_source` is `platform_default` or `request`, so you can tell
whether your `width`/`height` override was applied.

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

**There are two error shapes, not one.** A front-end has to handle both.

### 1. Application errors — `{ code, message, details }`

Raised by the pipeline itself. `code` is stable and safe to branch on.

```json
{ "code": "insufficient_source_text",
  "message": "source_text contains only 2 words, which is not enough to write a headline without inventing information.",
  "details": { "word_count": 2, "minimum": 5 } }
```

| Code | Status | Meaning |
|---|---|---|
| `invalid_request` | 422 | Sent only one of width/height, or a size outside 64–3840 |
| `invalid_image` | 422 | File empty, unreadable, not JPEG/PNG/WebP, or over 30 MB |
| `unsupported_asset` | 422 | Asset type not valid for that platform, or a logo slot |
| `insufficient_source_text` | 422 | Fewer than 5 words — not enough to write from |
| `copy_generation_failed` | 502 | The headline broke the accuracy rules twice |
| `rendering_failed` | 502 | The image model failed |
| `configuration_error` | 500 | `OPENAI_API_KEY` is not set |

`unsupported_asset` and `invalid_image` put useful data in `details` —
`valid_asset_types` for the platform, or the format that was detected.

### 2. Validation errors — `{ "detail": [ ... ] }`

FastAPI rejects these before your code runs, so **there is no `code` field**.
Reading `error.code` on one of these gives you `undefined`.

```json
{ "detail": [ { "type": "missing", "loc": ["body", "image"],
               "msg": "Field required", "input": null } ] }
```

You get this shape when a required field is absent, when `platform`,
`asset_type` or `quality` is not one of the allowed values, or when `image`
arrives as a string instead of a file part. Also `405 {"detail": "Method Not
Allowed"}` for the wrong HTTP method.

Defensive read: `body.code ?? body.detail?.[0]?.msg ?? "Request failed"`.

### Cost

Every 422 above happens **before** either AI call, so a bad request costs
nothing. Only `copy_generation_failed` and `rendering_failed` (502) come after
money has been spent.

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

158 tests, all passing. Both AI calls are faked, so the suite is free to run
and needs no API key.

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
