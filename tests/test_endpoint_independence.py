"""Proof that the two endpoints do not depend on each other.

Production sends `/ad-images/copy-options` and `/ad-images/render` as two
separate HTTP requests, possibly to two different worker processes behind a
load balancer, possibly minutes apart, possibly never both. Nothing may be
carried between them.

These tests exist to fail loudly if a cache, a session, a draft id or any other
shared state is ever introduced.
"""

from __future__ import annotations

import importlib
import pkgutil

import pytest

import app as app_package
from tests.conftest import make_image

OPTIONS = "/api/v1/ad-images/copy-options"
RENDER = "/api/v1/ad-images/render"

BRIEF = (
    "Handmade sourdough baked fresh every morning at our downtown bakery. "
    "Call us to reserve a loaf."
)
OTHER_BRIEF = (
    "Wide-plank wood-look flooring that brings warmth and elegance to any room "
    "with a view."
)


def options_call(client, *, image=None, brief=BRIEF, **extra):
    data = {"source_text": brief, "platform": "meta", "asset_type": "feed_square"}
    data.update(extra)
    return client.post(
        OPTIONS,
        files={"image": ("a.png", image or make_image(), "image/png")},
        data=data,
    )


def render_call(client, *, image=None, brief=None, **extra):
    data = {"platform": "meta", "asset_type": "feed_square"}
    if brief is not None:
        data["source_text"] = brief
    data.update(extra)
    return client.post(
        RENDER,
        files={"image": ("b.png", image or make_image(), "image/png")},
        data=data,
    )


# --- neither call needs the other ------------------------------------------


def test_render_works_without_ever_calling_copy_options(client):
    """A caller that already has words never has to touch the other endpoint."""
    response = render_call(client, headline="Warmth That Stays", placement="top_left")
    assert response.status_code == 200
    assert response.json()["copy_source"] == "caller"


def test_copy_options_works_without_ever_rendering(client, stub_model):
    """And a caller may ask for words and walk away."""
    assert options_call(client).status_code == 200
    assert stub_model.calls == 0


def test_render_before_options_is_fine(client):
    """Order is not a state machine. Render, then ask for options, then render."""
    assert render_call(client, headline="Warmth That Stays").status_code == 200
    assert options_call(client).status_code == 200
    assert render_call(client, headline="Colour That Lasts").status_code == 200


# --- nothing is carried between the two calls ------------------------------


def test_a_different_photograph_may_be_sent_to_each_call(client):
    """Proof there is no server-side binding to the first image.

    A production UI would send the same File twice, but the service has no way
    to notice if it does not -- there is nothing to compare against.
    """
    options = options_call(client, image=make_image(600, 600))
    assert options.status_code == 200
    chosen = options.json()["options"][0]

    render = render_call(
        client,
        image=make_image(1400, 900, fmt="JPEG"),
        headline=chosen["headline"],
        placement=chosen["placement"],
    )
    assert render.status_code == 200
    # The render measured the image it was actually given, not the earlier one.
    assert render.json()["source_image"]["width"] == 1400


def test_a_different_slot_may_be_sent_to_each_call(client):
    """No session pins the platform or asset type either."""
    assert options_call(client, platform="meta", asset_type="feed_square").status_code == 200
    render = render_call(
        client,
        platform="google_ads_pmax",
        asset_type="landscape",
        headline="Warmth That Stays",
    )
    assert render.status_code == 200
    assert render.json()["asset"]["platform"] == "google_ads_pmax"


def test_a_render_still_works_when_the_brief_is_sent_too(client):
    """Optional means optional -- sending it changes nothing about the words."""
    response = render_call(client, brief=BRIEF, headline="Warmth That Stays")
    assert response.status_code == 200
    assert response.json()["ad_copy"]["headline"] == "Warmth That Stays"


def test_the_render_never_sees_the_brief_from_the_options_call(client, stub_model):
    """The words are the only thing that crosses between the two calls."""
    options_call(client, brief=BRIEF)
    render_call(client, headline="Warmth That Stays")
    assert "sourdough" not in stub_model.last_prompt
    assert "bakery" not in stub_model.last_prompt.lower()


def test_each_options_call_answers_only_its_own_brief(client, stub_copy):
    """Two different briefs in a row must not bleed into one another."""
    options_call(client, brief=BRIEF)
    assert stub_copy.last_source_text == BRIEF
    options_call(client, brief=OTHER_BRIEF)
    assert stub_copy.last_source_text == OTHER_BRIEF


# --- each endpoint touches only the model it needs -------------------------


def test_options_never_reaches_the_image_model(client, stub_model, stub_copy):
    options_call(client)
    assert stub_copy.option_calls == 1
    assert stub_copy.calls == 0          # not the single-copy path either
    assert stub_model.calls == 0         # and nothing was rendered


def test_caller_words_never_reach_the_copy_model(client, stub_model, stub_copy):
    render_call(client, headline="Warmth That Stays")
    assert stub_copy.calls == 0
    assert stub_copy.option_calls == 0
    assert stub_model.calls == 1


# --- structural: there is nowhere to keep state even if someone tried ------


def test_the_controller_accumulates_nothing_across_requests(client):
    """Its attributes are the two model clients and settings -- and stay that
    way. A cache added later would show up here."""
    from app.api.dependencies import get_controller

    controller = get_controller()
    before = dict(controller.__dict__)

    options_call(client)
    render_call(client, headline="Warmth That Stays")
    render_call(client, brief=BRIEF)

    after = dict(controller.__dict__)
    assert set(before) == set(after)
    for key in before:
        assert before[key] is after[key], f"{key} was replaced during a request"


def test_no_module_level_mutable_collections(client):
    """Every module-level dict/list/set in the app is a NAMED CONSTANT.

    A per-request accumulator -- a draft cache, a session store -- would be a
    lowercase module-level dict, and this test would name it.
    """
    offenders: list[str] = []
    for info in pkgutil.walk_packages(app_package.__path__, "app."):
        module = importlib.import_module(info.name)
        for name, value in vars(module).items():
            if name.startswith("__") or not isinstance(value, (dict, list, set)):
                continue
            if getattr(value, "__module__", None):        # skip imported types
                continue
            bare = name.lstrip("_")
            if not bare.isupper():
                offenders.append(f"{info.name}.{name}")
    assert offenders == [], f"mutable module-level state found: {offenders}"


@pytest.mark.parametrize("run", range(3))
def test_repeating_the_same_pair_is_deterministic(client, run):
    """Nothing warms up, nothing degrades, nothing leaks between runs."""
    options = options_call(client)
    assert options.status_code == 200
    assert len(options.json()["options"]) == 3
    render = render_call(client, headline="Warmth That Stays")
    assert render.status_code == 200
    assert render.json()["copy_source"] == "caller"
