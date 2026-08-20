"""The deterministic gate on whatever the copy model returns."""

import asyncio

import pytest

from app.core.config import Settings
from app.core.errors import CopyGenerationError
from app.domain import platforms
from app.domain.platforms import AssetType, Platform
from app.services.copy_service import (
    AdCopy,
    AdCopyDraft,
    AdCopyOptions,
    CopyService,
    Placement,
    check_copy,
)

META_SQUARE = platforms.resolve(Platform.META, AssetType.FEED_SQUARE)  # 8 words
SIDEBAR = platforms.resolve(Platform.WEBSITE, AssetType.SIDEBAR_CARD)  # 5, no sub

SOURCE = (
    "Give your space a fresh new look! Professional painting, clean finishes, "
    "and quality results you can count on. Book your free estimate today!"
)


def copy(headline, subheadline=None, placement=Placement.BOTTOM_LEFT):
    return AdCopy(
        headline=headline,
        subheadline=subheadline,
        placement=placement,
        source_support="Professional painting, clean finishes",
    )


def test_clean_copy_passes():
    assert check_copy(copy("Fresh Colour, Flawless Finish"), SOURCE, META_SQUARE) == []


def test_empty_headline_is_rejected():
    problems = check_copy(copy("   "), SOURCE, META_SQUARE)
    assert any("empty" in p for p in problems)


def test_transcribing_the_whole_brief_is_rejected():
    """The exact failure this stage exists to prevent."""
    problems = check_copy(copy(SOURCE), SOURCE, META_SQUARE)
    assert any("Distil it further" in p for p in problems)


def test_headline_over_budget_is_rejected():
    problems = check_copy(
        copy("A headline that runs on for far too many words indeed"),
        SOURCE,
        META_SQUARE,
    )
    assert any("the limit for" in p for p in problems)


def test_headline_at_the_budget_passes():
    assert check_copy(copy("One two three four five six seven eight"),
                      SOURCE, META_SQUARE) == []


def test_supporting_line_rejected_where_the_canvas_forbids_one():
    problems = check_copy(copy("Fresh Colour", "Clean finishes"), SOURCE, SIDEBAR)
    assert any("takes no supporting line" in p for p in problems)


def test_supporting_line_over_budget_is_rejected():
    problems = check_copy(
        copy("Fresh Colour", "a b c d e f g h i j k l m"), SOURCE, META_SQUARE
    )
    assert any("supporting line is" in p for p in problems)


@pytest.mark.parametrize(
    "headline",
    ["Book Now For Fresh Colour", "Fresh Colour — Call Today",
     "Learn More About Our Work", "Get A Quote Today"],
)
def test_call_to_action_headlines_are_rejected(headline):
    problems = check_copy(copy(headline), SOURCE, META_SQUARE)
    assert any("call-to-action" in p for p in problems)


def test_invented_figures_are_rejected():
    problems = check_copy(copy("Save 40% On Painting"), SOURCE, META_SQUARE)
    assert any("do not appear in the brief" in p for p in problems)
    assert any("40%" in p for p in problems)


def test_invented_prices_are_rejected():
    problems = check_copy(copy("Rooms Painted From $99"), SOURCE, META_SQUARE)
    assert any("do not appear in the brief" in p for p in problems)


def test_figures_present_in_the_brief_are_allowed():
    source = "We have painted over 500 homes across the county since 2010."
    assert check_copy(copy("500 Homes Transformed"), source, META_SQUARE) == []


def test_thousands_separator_does_not_trigger_a_false_positive():
    source = "We have painted over 1,200 homes."
    assert check_copy(copy("1200 Homes Painted"), source, META_SQUARE) == []


def test_multiple_violations_are_all_reported():
    problems = check_copy(
        copy("Book Now And Save 40% On Every Single Room Today"),
        SOURCE,
        META_SQUARE,
    )
    assert len(problems) >= 3


# --- several options at once ------------------------------------------------


def option_set(*pairs, placement=Placement.BOTTOM_LEFT):
    """An AdCopyOptions batch from (headline, subheadline) pairs.

    Placement is one value for the whole batch, as the schema requires.
    """
    return AdCopyOptions(
        options=[
            AdCopyDraft(
                headline=headline,
                subheadline=subheadline,
                source_support="Professional painting, clean finishes",
            )
            for headline, subheadline in pairs
        ],
        placement=placement,
    )


def service_returning(*batches):
    """A CopyService whose model call returns each batch in turn."""
    service = CopyService(Settings(openai_api_key="test-key"))
    calls = []

    async def fake_call(instructions, content, text_format=None):
        calls.append((instructions, content))
        return batches[min(len(calls) - 1, len(batches) - 1)]

    service._call = fake_call
    service.calls = calls
    return service


def write_options(service, spec=META_SQUARE, source_text=SOURCE, count=3):
    return asyncio.run(
        service.write_options(
            image_png=b"png",
            source_text=source_text,
            spec=spec,
            width=1080,
            height=1080,
            count=count,
        )
    )


def test_three_clean_options_come_back_in_order():
    service = service_returning(
        option_set(
            ("Fresh Colour, Flawless Finish", "Careful prep, clean edges"),
            ("Colour That Lasts", None),
            ("Every Wall, Considered", None),
        )
    )
    result = write_options(service)
    assert [o.headline for o in result.options] == [
        "Fresh Colour, Flawless Finish",
        "Colour That Lasts",
        "Every Wall, Considered",
    ]
    assert result.rejected == []
    assert len(service.calls) == 1


def test_a_supporting_line_stays_optional_per_option():
    service = service_returning(
        option_set(
            ("Fresh Colour", "Careful prep, clean edges"),
            ("Colour That Lasts", None),
            ("Every Wall, Considered", None),
        )
    )
    result = write_options(service)
    assert result.options[0].subheadline == "Careful prep, clean edges"
    assert result.options[1].subheadline is None


def test_an_option_that_breaks_the_rules_is_dropped_not_returned():
    """The caller must never be offered a headline that cannot be rendered."""
    service = service_returning(
        option_set(
            ("Fresh Colour, Flawless Finish", None),
            ("Save 40% This Week", None),          # figure not in the brief
            ("Every Wall, Considered", None),
        ),
        option_set(("Colour That Lasts", None)),   # the replacement
    )
    result = write_options(service)
    headlines = [o.headline for o in result.options]
    assert "Save 40% This Week" not in headlines
    assert any("do not appear in the brief" in r for r in result.rejected)
    assert len(service.calls) == 2


def test_the_second_attempt_is_told_what_was_wrong():
    service = service_returning(
        option_set(("Save 40% This Week", None)),
        option_set(("Colour That Lasts", None)),
    )
    write_options(service)
    retry_text = str(service.calls[1][1])
    assert "do not appear in the brief" in retry_text
    assert "replacements" in retry_text


def test_three_rewordings_of_one_line_are_not_three_options():
    service = service_returning(
        option_set(
            ("Colour That Lasts", None),
            ("colour that lasts", None),
            ("COLOUR THAT LASTS", None),
        )
    )
    result = write_options(service)
    assert len(result.options) == 1


def test_nothing_usable_is_an_error_not_an_empty_list():
    bad = option_set(("Save 40% This Week", None))
    service = service_returning(bad, bad)
    with pytest.raises(CopyGenerationError):
        write_options(service)


def test_more_options_than_asked_for_are_trimmed():
    service = service_returning(
        option_set(
            ("Fresh Colour", None),
            ("Colour That Lasts", None),
            ("Every Wall, Considered", None),
            ("Done Properly", None),
        )
    )
    assert len(write_options(service, count=3).options) == 3


def test_every_option_gets_the_one_placement_decided_for_the_photo():
    service = service_returning(
        option_set(
            ("Fresh Colour", None),
            ("Colour That Lasts", None),
            ("Every Wall, Considered", None),
            placement=Placement.TOP_RIGHT,
        )
    )
    result = write_options(service)
    assert {o.placement for o in result.options} == {Placement.TOP_RIGHT}


def test_a_retry_cannot_move_the_placement_of_kept_options():
    """The first batch settles it; replacements inherit that decision."""
    service = service_returning(
        option_set(("Fresh Colour", None), ("Save 40% This Week", None),
                   placement=Placement.BOTTOM_CENTER),
        option_set(("Colour That Lasts", None), placement=Placement.TOP_LEFT),
    )
    result = write_options(service)
    assert len(service.calls) == 2
    assert {o.placement for o in result.options} == {Placement.BOTTOM_CENTER}


def test_the_slot_rules_still_apply_to_every_option():
    """A supporting line where the canvas has no room for one is dropped."""
    service = service_returning(
        option_set(("Fresh Colour", "Clean finishes"), ("Colour Lasts", None)),
        option_set(("Done Properly", None)),
    )
    result = write_options(service, spec=SIDEBAR)
    assert all(o.subheadline is None for o in result.options)
    assert any("takes no supporting line" in r for r in result.rejected)
