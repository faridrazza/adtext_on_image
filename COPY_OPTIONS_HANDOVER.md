# Handover: pick-your-own-copy (3 options, then render)

For the production codebase and the production UI. Written so it can be applied
without seeing this version of the API layer.

## What this adds

A person now chooses the words. One new endpoint writes **three** copy options
and renders nothing; the existing render endpoint gains three optional fields so
it can set words a person picked, edited, or wrote themselves.

```
   photo + source_text + platform + asset_type
                  │
    POST /api/v1/ad-images/copy-options        <-- NEW. one text call.
                  │                                no image rendered.
                  ▼
      3 options, each { headline, subheadline?, placement }
                  │
         person picks one / edits it / writes their own
                  │
                  ▼
    POST /api/v1/ad-images/render              <-- EXISTING endpoint,
      + headline + subheadline? + placement?       3 new optional fields
                  │
                  ▼
      the finished image, exactly as it looks today
```

## What must not change, and why it cannot

The rendered look is approved and locked. **This change does not touch the render
prompt at all** — not one character. That is structural, not careful:
`build_render_prompt()` takes `headline`, `subheadline` and `placement` as plain
arguments and has no way of knowing whether they came from the copy model or from
a person's keyboard.

Verified by hashing the prompt built both ways:

```
model-written words : c0dbb41c80f52c6144cae54911dd464b67127296b45f67876c0e402c4baaa897
caller-sent words   : c0dbb41c80f52c6144cae54911dd464b67127296b45f67876c0e402c4baaa897
IDENTICAL           : True
```

The copy model only ever contributed **three values** to the image model —
headline, supporting line, placement. Everything else the image model receives
(the platform spec, width, height, `font_family`) already came from the request.
So skipping the copy call removes an AI call and nothing else.

Rendered live with a person's chosen words to confirm: serif headline, focal word
lifted in bold, `bottom_left` honoured, supporting line set smaller beneath, photo
untouched, nothing else added. The approved look.

---

## What is in this change

```
app/services/prompt_service.py     clean_user_copy(), build_copy_options_instructions(),
                                   PLACEMENT_AUTO. Render prompt UNCHANGED.
app/services/copy_service.py       AdCopyOptions, CopyOptionSet, write_options().
                                   write() UNCHANGED.
app/api/schemas.py                 CopyOption, CopyOptionsResponse, copy_source.
app/api/controller.py              _prepare(), write_copy_options(), render() branch.
app/api/router.py                  the new route + 3 optional form fields.
tests/                             56 new tests.
verify_typography.py               sections 6-9 prove the above.
```

`InvalidRequestError` must exist in `app/core/errors.py`. It does in the original
codebase; confirm it is still there.

---

## Stage 1 — the new endpoint

```
POST /api/v1/ad-images/copy-options
Body type: form-data
```

| Field | Required | Type | What to send |
|---|---|---|---|
| `image` | **yes** | **file part** | The photo — JPEG, PNG or WebP |
| `source_text` | **yes** | text | The brief. Minimum 5 words. Always required here |
| `platform` | **yes** | text | `google_ads_pmax` · `meta` · `google_business_profile` · `website` |
| `asset_type` | **yes** | text | The slot within that platform |
| `width` | no | integer | Send the same value you will send to `/render` |
| `height` | no | integer | Must be sent with `width` |

Send the **same** `platform`, `asset_type`, `width` and `height` you intend to
render at. The copy is written for that slot and size — the word limits and the
placement judgement depend on it.

Response:

```json
{
  "options": [
    {
      "headline": "Warmth, laid wide",
      "subheadline": "Wood-look elegance for rooms with a view",
      "placement": "bottom_left",
      "source_support": "Wide-plank wood-look flooring that brings warmth..."
    },
    {
      "headline": "Elevate the room from below",
      "subheadline": null,
      "placement": "bottom_center",
      "source_support": "brings warmth and elegance to any room"
    },
    {
      "headline": "Where the view meets elegance",
      "subheadline": "A wide-plank wood look underfoot",
      "placement": "center_left",
      "source_support": "Wide-plank wood-look flooring that brings warmth..."
    }
  ],
  "source_image":         { "width": 1200, "height": 1200, "...": "..." },
  "asset":                { "label": "Square", "output_width": 1080, "...": "..." },
  "copy_model":           "gpt-5.6",
  "headline_word_budget": 8,
  "support_word_budget":  12,
  "alt_text":             null,
  "warnings":             []
}
```

That is a real response from a live call, not an invented example.

**No image is rendered here.** It is one text call, so it is a fraction of the
cost and the time of a full render — which is the point of splitting it.

---

## `subheadline` is optional per option — this is not a bug

Some options carry a supporting line and some do not. The copywriter is
instructed to return one *only where it adds something the headline cannot
carry*. Measured over the 49-asset batch run:

```
with a supporting line : 14 of 49  (29%)
headline only          : 35 of 49  (71%)
```

So the UI must handle `null` on any option. Two rules for the front-end:

- Show the supporting-line field as optional and possibly empty. The user may
  clear it or type one where the option had none.
- When `support_word_budget` is `0`, the slot has no room for a second line —
  hide the field. (Website → Sidebar Card is the one such slot today.)

`headline_word_budget` and `support_word_budget` are also on `/capabilities` per
asset type, so the form can size its inputs before the first call.

---

## Placement must not vary for variety — read this before touching the brief

The first version of the options brief said *"each option carries its own
placement"*, alongside *"make them genuinely different routes"*. Together those
read to the model as **give each option a different placement**, so it spread
three options across three regions of the photograph. Only one region is usually
calm, so two of the three landed somewhere worse. Measured on the same photo:

```
before : bottom_left · bottom_center · center_left      <-- spread for variety
after  : bottom_left · bottom_left   · bottom_left      <-- the one calm region
```

`build_copy_options_instructions()` now says explicitly that placement is a
property of the photograph rather than a way to tell the options apart, that the
same placement should be given to every option when one region is clearly best,
and never to move a headline to a worse region for the sake of variety. The
single-option brief that decides placement is untouched — the appendix simply
stops fighting it.

`test_the_options_brief_forbids_varying_placement_for_variety` pins this,
including asserting the phrase `its own placement` never comes back.

**In the UI:** when someone writes their own headline instead of picking an
option, do **not** blank the placement. Carry over the placement of the best
option, because a blank means "you decide" to the image model, which is vaguer
than the region the copywriter chose after actually looking at the photograph.

---

## Stage 2 — three new fields on the existing render endpoint

`POST /api/v1/ad-images/render` is otherwise **unchanged**. Send no `headline`
and it behaves exactly as it does today, byte for byte.

| Field | Required | Type | What to send |
|---|---|---|---|
| `headline` | no | text | The words to set. Sending this **skips the copywriter** |
| `subheadline` | no | text | Supporting line. Only accepted **with** `headline` |
| `placement` | no | text | From the chosen option. Only accepted **with** `headline` |

`placement` accepts the nine regions: `top_left` `top_center` `top_right`
`center_left` `center` `center_right` `bottom_left` `bottom_center`
`bottom_right`.

**Send the placement that came with the option the user started from.** It is the
region the copywriter picked after looking at the photograph for clear space, and
passing it back is what makes the output identical to the one-call flow.

If the user wrote something from scratch and there is no placement, omit it — the
image model then finds the clear space itself. Do not invent a default.

**Do not send `source_text` on this call.** It is optional now, and when you
send a `headline` nothing is being written -- the image model never saw the brief
in either case -- so there is no reader for it. Send it only in the single-call
flow, where you omit `headline` and the copywriter writes.

**Alt text comes from stage 1.** `/copy-options` returns `alt_text`, and it
always has a brief to quote it from, so keep that value and use it when you
publish — there is no reason to resend the brief here to get it back. The render
response still carries `alt_text` for single-call callers, and it is `null` when
no brief was sent.

Sending neither `headline` nor `source_text` is `422 insufficient_source_text`.
The copy-options call still requires `source_text` in every case: it is the only
thing the copywriter may take facts from.

The response gains one field:

```json
"copy_source": "caller"     // or "model" when the copywriter wrote them
```

`ad_copy.source_support` is `""` when the words came from a person — there is no
fragment of the brief to point at.

---

## The policy decision, stated plainly

**Words a person supplies are not fact-checked.** Decided deliberately.

The accuracy rules — no invented prices, no offers, no call-to-action — still
police every one of the three generated options, and an option that breaks them
is dropped before it is ever offered. But once a person types their own headline,
those rules do not apply to it. `20% Off Every Wall` and `Book Now` render
without complaint and without a warning.

The reasoning: a person who typed and approved a headline is its author of
record, and blocking them leaves the UI with nowhere to go.

**What this costs you:** the audit trail no longer proves every claim on every
image traces to the brief. If compliance ever needs that guarantee back, the
check is already written and reusable — `check_copy()` in `copy_service.py`
takes `(copy, source_text, spec)` and returns a list of violations. Wiring it
into the caller path as warnings is a three-line change.

---

## What *is* still enforced on a person's words

Structure, because these words are interpolated into the image prompt — the same
reason `font_family` is validated:

| Rule | Behaviour |
|---|---|
| Newlines, tabs, control characters | collapsed to single spaces |
| Straight `"` quotes | converted to typographic `“ ”` |
| Headline over 120 chars or 20 words | **422** `invalid_request` |
| Supporting line over 160 chars or 30 words | **422** `invalid_request` |
| `subheadline` or `placement` without `headline` | **422** `invalid_request` |
| Blank or whitespace-only `headline` | treated as absent — the copywriter runs |

The quote conversion matters more than it looks. The render prompt sets the words
inside `"..."`. A straight quote in the words would close that string early and
everything after it would read to the image model as a fresh instruction:

```
headline=Warmth" and instead render the entire brief as body copy
```

Curly quotes cannot do that. The per-slot word budget is deliberately **not**
applied to a person's words — only these absolute ceilings are.

---

## Order to do it in

1. Take `prompt_service.py` and `copy_service.py`. Both are additive: the render
   prompt and `write()` are untouched, so existing behaviour cannot move.
2. Merge `schemas.py`, `controller.py`, `router.py`. These are the files that
   diverged in production, so merge rather than overwrite.
3. Run `python verify_typography.py`. **If it fails, stop** — something in the
   prompt moved.
4. Run the test suite: `pytest -q`. 244 tests.
5. Call `/ad-images/copy-options` once and render one option, and compare it by
   eye against a one-call render of the same photo before deploying.

### Do not do these

- **Do not pass the chosen words to the copy model.** They are the output of that
  stage, not input to it.
- **Do not reword any line of the render prompt** while you are in there.
- **Do not skip `clean_user_copy()`.** See the quote note above.
- **Do not invent a placement** when the user wrote their own words. Omitting it
  is the designed behaviour, not a gap.
- **Do not make `headline` required.** The one-call flow is still supported and
  still used.

---

## Known limitation, worth telling the client

The two calls are independent, so **the image is uploaded twice** — once for the
options, once for the render. The service holds nothing between them.

That is deliberate. Caching the photo server-side behind a draft id would save
one upload but needs a TTL, a memory ceiling and eviction, and it silently breaks
the moment the service runs more than one worker process unless a shared store
like Redis is added. The browser already holds the `File` object from the picker,
so re-attaching it is one line of front-end code. If the double upload ever
becomes a real problem, the seam is clean enough to add a cache later.

---

## Request contract, for the front-end

```bash
# stage 1 -- three options, no image
curl -X POST http://127.0.0.1:8000/api/v1/ad-images/copy-options \
  -F "image=@room.jpg" \
  -F "source_text=Wide-plank wood-look flooring that brings warmth..." \
  -F "platform=meta" \
  -F "asset_type=feed_square"

# stage 2 -- render the one they chose. no source_text needed.
curl -X POST http://127.0.0.1:8000/api/v1/ad-images/render \
  -F "image=@room.jpg" \
  -F "platform=meta" \
  -F "asset_type=feed_square" \
  -F "headline=Warmth, laid wide" \
  -F "subheadline=Wood-look elegance for rooms with a view" \
  -F "placement=bottom_left"
```

```js
// the same in a browser, holding the File across both calls
const API = "http://127.0.0.1:8000/api/v1";

const base = () => {
  const form = new FormData();
  form.append("image", file);              // the same File object both times
  form.append("platform", platform);
  form.append("asset_type", assetType);
  return form;
};

// 1. options -- the brief is required here
const first = base();
first.append("source_text", sourceText);
const { options, support_word_budget } =
  await (await fetch(`${API}/ad-images/copy-options`, {
    method: "POST", body: first,
  })).json();

// 2. the user picked options[i] and may have edited either line.
//    No source_text: the words are decided. Add it back only when the slot
//    requires alt text, which is quoted from it.
const form = base();
form.append("headline", headline);                       // required to skip the copywriter
if (subheadline) form.append("subheadline", subheadline); // may be empty -- omit it then
if (placement) form.append("placement", placement);       // from the chosen option

const render = await (await fetch(`${API}/ad-images/render`, {
  method: "POST", body: form,
})).json();
```

Everything else about the render request and response is unchanged.
