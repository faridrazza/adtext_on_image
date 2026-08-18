"""The deterministic gate on whatever the copy model returns."""

import pytest

from app.domain import platforms
from app.domain.platforms import AssetType, Platform
from app.services.copy_service import AdCopy, Placement, check_copy

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
