"""Prompt construction and source-text policy.

Two prompts, for two different jobs:

* :func:`build_copy_instructions` briefs a **text model** that reads the
  photograph, the source text and the placement, and decides what the headline
  should say. This is where copywriting judgement lives.
* :func:`build_render_prompt` briefs the **image model**, and contains only the
  final approved words. It never sees the source text -- that is what stops the
  render from transcribing the input instead of distilling it.

Keeping them apart matters: an image model handed a block of source text will
set that text on the image, because a literal string near the end of an image
prompt reads as "render this".
"""

from __future__ import annotations

import re

from app.core.errors import InsufficientSourceTextError, InvalidRequestError
from app.domain.platforms import AssetSpec, AssetType

# Below this, there is nothing honest to build a headline from.
MIN_SOURCE_WORDS = 5
# Below this we still render, but the caller should know the copy will be thin.
THIN_SOURCE_WORDS = 12

MAX_ALT_TEXT_CHARS = 125

# A brand-kit typeface name: "Arial", "Helvetica Neue", "Gill Sans MT".
# The value reaches the image model verbatim, so it must read as a font
# name and nothing else -- a sentence here would be read as instructions.
MAX_FONT_FAMILY_CHARS = 40
_FONT_FAMILY_PATTERN = re.compile("^[A-Za-z][A-Za-z0-9 .+&'-]*$")

# Caller-supplied words reach the image prompt verbatim, exactly as
# font_family does. These are safety ceilings, not the per-slot legibility
# budget in _WORD_BUDGETS: a human who has chosen their own words is trusted
# with them, but an instruction-shaped paragraph must not reach the model.
MAX_USER_HEADLINE_CHARS = 120
MAX_USER_HEADLINE_WORDS = 20
MAX_USER_SUPPORT_CHARS = 160
MAX_USER_SUPPORT_WORDS = 30

# Newlines and control characters would break the single quoted line the
# render prompt puts the words on.
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f-\x9f]")

# How many copy options the options endpoint asks for.
COPY_OPTION_COUNT = 3

# Headline / supporting-line word budgets. Small canvases get fewer words
# because anything longer stops being legible at render size.
_WORD_BUDGETS: dict[AssetType, tuple[int, int]] = {
    AssetType.SIDEBAR_CARD: (5, 0),
    AssetType.LANDSCAPE: (7, 10),
    AssetType.FACEBOOK_LANDSCAPE: (7, 10),
    AssetType.STORY_REEL: (7, 10),
    AssetType.HERO: (9, 14),
}
_DEFAULT_WORD_BUDGET = (8, 12)


def assess_source_text(source_text: str) -> list[str]:
    """Enforce that there is enough substance to build honest copy from.

    Raises when the text cannot support a headline; returns warnings when it
    only just can.
    """
    cleaned = source_text.strip()
    if not cleaned:
        raise InsufficientSourceTextError(
            "source_text is empty. The source text is the only permitted basis "
            "for the ad copy, so nothing can be generated."
        )

    word_count = len(cleaned.split())
    if word_count < MIN_SOURCE_WORDS:
        raise InsufficientSourceTextError(
            f"source_text contains only {word_count} words, which is not enough "
            "to write a headline without inventing information.",
            details={"word_count": word_count, "minimum": MIN_SOURCE_WORDS},
        )

    if word_count < THIN_SOURCE_WORDS:
        return [
            f"source_text is short ({word_count} words); the generated copy will "
            "be minimal by design rather than padded with unsupported claims."
        ]
    return []


def derive_alt_text(source_text: str, spec: AssetSpec) -> str | None:
    """Alt text taken verbatim from the source text, never invented.

    Only produced for assets whose platform requires it.
    """
    if not spec.requires_alt_text:
        return None
    first_sentence = re.split(r"(?<=[.!?])\s+", source_text.strip())[0].strip()
    if not first_sentence:
        return None
    if len(first_sentence) <= MAX_ALT_TEXT_CHARS:
        return first_sentence
    return first_sentence[: MAX_ALT_TEXT_CHARS - 1].rstrip() + "…"


def clean_font_family(value: str | None) -> str | None:
    """Validate a brand-kit typeface name, or None when none was sent.

    Collapses whitespace and refuses anything that is not plainly a font
    name, because the value is interpolated into the image prompt.
    """
    if value is None:
        return None
    cleaned = " ".join(value.split())
    if not cleaned:
        return None
    if len(cleaned) > MAX_FONT_FAMILY_CHARS:
        raise InvalidRequestError(
            f"font_family is {len(cleaned)} characters; the maximum is "
            f"{MAX_FONT_FAMILY_CHARS}.",
            details={"font_family": value[:80],
                     "maximum": MAX_FONT_FAMILY_CHARS},
        )
    if not _FONT_FAMILY_PATTERN.match(cleaned):
        raise InvalidRequestError(
            "font_family must be a typeface name -- letters, digits, spaces "
            "and the characters . ' - + & only, starting with a letter.",
            details={"font_family": value[:80]},
        )
    return cleaned


def _curly(value: str) -> str:
    """Turn straight double quotes into typographic ones, in pairs.

    The render prompt sets the words inside "..."; a straight quote in the
    words would close that string early and everything after it would read to
    the image model as a fresh instruction. Curly quotes cannot, and they are
    what a designer would set anyway.
    """
    out = []
    opening = True
    for char in value:
        if char == '"':
            out.append("“" if opening else "”")
            opening = not opening
        else:
            out.append(char)
    return "".join(out)


def clean_user_copy(
    value: str | None,
    *,
    field: str,
    max_chars: int,
    max_words: int,
) -> str | None:
    """Sanitise words the caller chose themselves, or None when none were sent.

    Deliberately does **not** apply the accuracy or call-to-action rules: those
    police what the *model* may invent, and a person who has typed and approved
    their own headline is the author of record. What this does police is
    structure, because the value is interpolated into the image prompt.
    """
    if value is None:
        return None
    cleaned = " ".join(_CONTROL_CHARS.sub(" ", value).split())
    if not cleaned:
        return None
    if len(cleaned) > max_chars:
        raise InvalidRequestError(
            f"{field} is {len(cleaned)} characters; the maximum is "
            f"{max_chars}.",
            details={field: cleaned[:120], "maximum_chars": max_chars},
        )
    words = len(cleaned.split())
    if words > max_words:
        raise InvalidRequestError(
            f"{field} is {words} words; the maximum is {max_words}. This is a "
            "headline, not a paragraph.",
            details={field: cleaned[:120], "maximum_words": max_words},
        )
    return _curly(cleaned)


def word_budget(spec: AssetSpec) -> tuple[int, int]:
    return _WORD_BUDGETS.get(spec.asset_type, _DEFAULT_WORD_BUDGET)


# --------------------------------------------------------------------------
# Stage 1 -- the copywriter (text model, sees the photograph)


def build_copy_instructions(
    spec: AssetSpec, width: int, height: int
) -> str:
    """Brief the model that decides what the headline should say."""
    headline_words, support_words = word_budget(spec)

    support_rule = (
        f"A supporting line of at most {support_words} words is optional. Use "
        "it only when it adds something the headline cannot carry. When in "
        "doubt, return null -- one strong line beats two weak ones."
        if support_words
        else "Return null for the supporting line. This placement is too small "
        "for a second line of text."
    )

    layout = f" {spec.layout_guidance}" if spec.layout_guidance else ""

    return f"""\
You are a senior advertising copywriter. You are shown a photograph and given a
brief. Decide the words that will be set over that photograph.

DISTIL — DO NOT TRANSCRIBE
The brief is raw input, not copy. It is usually a paragraph; your headline is a
few words. Never return the brief, a sentence from it, or a lightly reworded
version of it. Find the one idea worth saying and say it in as few words as
possible.
- Headline: at most {headline_words} words. Fewer is better.
- {support_rule}
- No trailing full stop on the headline. No quotation marks around it.
- No call-to-action ("Book Now", "Call Today", "Get a Quote"). Something else
  handles that. Write the message, not the button.

ACCURACY — NON-NEGOTIABLE
The brief is your only source of facts. You may rewrite its language however you
like; you may not add information it does not contain. Never state or imply:
prices, discounts or amounts; offers, deadlines, urgency or scarcity;
statistics, ratings, review counts or awards; guarantees, warranties or
certifications; superlatives ("best", "#1", "leading", "fastest"); services,
products or qualities not mentioned; or any brand name, phone number, address
or URL not present in the brief.
Before returning a line, check: can a reader point to the part of the brief that
makes this true? If not, rewrite it. Paraphrase freely; invent nothing.

USE THE PHOTOGRAPH
Look at what is actually in frame. The headline should feel like it belongs to
this image, not to any stock photo. Also decide where the text should sit: pick
the region with calm, uncluttered space, away from faces and the main subject.

WRITE FOR THE PLACEMENT
This runs as {spec.label} on {spec.platform.value}, at {width}x{height}px.{layout}
It has about one second to land. Plain, confident, human language. Cut corporate
filler ("solutions", "we strive to deliver", "your one-stop shop"). No clickbait
and no manufactured urgency.

Return the headline, the optional supporting line, the placement region, and the
exact fragment of the brief that supports your headline.\
"""


def build_copy_options_instructions(
    spec: AssetSpec,
    width: int,
    height: int,
    count: int = COPY_OPTION_COUNT,
) -> str:
    """Brief the copywriter for several options instead of one.

    Built by appending to :func:`build_copy_instructions`, never by restating
    it, so the word limits and the accuracy rules cannot drift apart from the
    single-option path.
    """
    return f"""{build_copy_instructions(spec, width, height)}

RETURN {count} OPTIONS
Return exactly {count} wordings in one list, plus one placement for the
photograph. Every rule above applies to each wording in full: the same word
limits, the same accuracy rules, the same supporting-line rule.

Make them genuinely different routes to the same truth, not one line reworded
{count} times. Vary the angle: what the reader gains, what they avoid, the craft
behind the work, the feeling of the place. No two options may open with the same
words.

The placement is a single decision about this photograph, made exactly as it
would be for one headline -- not one choice per option.

Order them best first -- option 1 is the one you would run.
Each option carries its own supporting line and its own supporting fragment,
because the reader may choose any one of them."""


# --------------------------------------------------------------------------
# Stage 2 -- the renderer (image model, never sees the source text)

# Sent as the placement when the caller supplied their own words and no
# placement. It is deliberately absent from _PLACEMENT_PHRASING below, so the
# prompt falls through to "over a calm area of the image" and the image model
# finds the clear space itself -- the same judgement the copy model would have
# made. Nothing about the prompt is special-cased for it.
PLACEMENT_AUTO = "auto"

_PLACEMENT_PHRASING = {
    "top_left": "in the upper-left area",
    "top_center": "across the upper area",
    "top_right": "in the upper-right area",
    "center_left": "on the left side, vertically centred",
    "center": "in the centre of the frame",
    "center_right": "on the right side, vertically centred",
    "bottom_left": "in the lower-left area",
    "bottom_center": "across the lower area",
    "bottom_right": "in the lower-right area",
}


def build_render_prompt(
    *,
    headline: str,
    subheadline: str | None,
    placement: str,
    spec: AssetSpec,
    width: int,
    height: int,
    font_family: str | None = None,
) -> str:
    """Brief the image model with the final words only.

    Deliberately short. Image models follow concise prompts far better than
    long rule lists, and there is no source text here to copy.
    """
    where = _PLACEMENT_PHRASING.get(placement, "over a calm area of the image")

    # With a brand kit the typeface is dictated; without one the model keeps
    # choosing it, exactly as it did before this parameter existed. Every
    # other typography instruction is identical in both cases.
    typeface = (
        f"- Set every word in {font_family}. Use that exact typeface and no "
        "other."
        if font_family
        else "- Choose a typeface with real character that suits the mood "
        "of this photograph."
    )

    second_line = (
        f'\nSupporting line, set smaller beneath it: "{subheadline}"'
        if subheadline
        else ""
    )

    layout = f"\n{spec.layout_guidance}" if spec.layout_guidance else ""

    return f"""\
Set this text over the supplied photograph, {where}.

Headline: "{headline}"{second_line}

Set exactly those words. Do not add, remove, reword or repeat any of them, and
do not add any other text.

Leave the photograph itself untouched: same colours, lighting, contrast, crop,
framing, people, faces and objects. Only the text is new.

Make the typography exceptional — work a brand-campaign art director would sign
off, not default type dropped on a photo:
{typeface}
- Make the headline dominant, with decisive size and weight contrast against any
  supporting line.
- Take the colour from the photograph: clean white or near-black, or an accent
  hue already present in the image.
- Consider lifting one key word with weight, size or colour so the line has a
  focal point.
- Align to a clear axis, break lines at natural phrase boundaries, keep line
  lengths even, and give it generous space.

Required: instantly legible, correct spelling, no garbled or doubled letters,
type upright and horizontal, at least 5% clear of every edge, never across a
face or the main subject. Where the background is busy, a soft, edgeless
darkening behind the text is allowed for contrast — never a visible panel, bar
or box.

Add nothing but the letters: no call-to-action, button, logo, badge, icon,
emoji, QR code, shape, frame, border, divider, sticker or decorative graphic.
Do not stretch, skew, arch or 3D-extrude the type. No drop shadows, bevels,
glows, outlines or metallic gradients.

Placement: {spec.label} on {spec.platform.value}, {width}x{height}px.{layout}\
"""
