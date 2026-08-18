"""Output-size planning against each model family's real constraints."""

import pytest

from app.services.openai_image_service import plan_size

MIN_PIXELS = 655_360
MAX_PIXELS = 8_294_400
MAX_EDGE = 3840


def test_size_divisible_by_16_is_native():
    plan = plan_size("gpt-image-2", 1536, 864)
    assert plan.native
    assert plan.size_param == "1536x864"
    assert plan.warnings == []


@pytest.mark.parametrize(
    ("requested", "expected"),
    [
        # 1080 is not divisible by 16, so Meta's core sizes round up by 8px.
        ((1080, 1080), "1088x1088"),
        ((1080, 1920), "1088x1920"),
        ((1080, 1350), "1088x1360"),
        ((1200, 628), "1200x640"),
    ],
)
def test_non_aligned_sizes_round_upward(requested, expected):
    """Rounding up means the correction is a downsample, which stays sharp."""
    plan = plan_size("gpt-image-2", *requested)
    assert plan.size_param == expected
    assert not plan.native
    assert len(plan.warnings) == 1


@pytest.mark.parametrize(
    "requested", [(300, 250), (300, 600), (64, 64), (1920, 600), (3840, 2160)]
)
def test_plans_always_satisfy_model_limits(requested):
    plan = plan_size("gpt-image-2", *requested)
    assert plan.width % 16 == 0 and plan.height % 16 == 0
    assert MIN_PIXELS <= plan.width * plan.height <= MAX_PIXELS
    assert max(plan.width, plan.height) <= MAX_EDGE


def test_extreme_aspect_ratio_falls_back_to_a_fixed_size():
    plan = plan_size("gpt-image-2", 3000, 400)  # 7.5:1, outside 1:3-3:1
    assert not plan.native
    assert (plan.width, plan.height) in {(1024, 1024), (1536, 1024), (1024, 1536)}


@pytest.mark.parametrize(
    ("model", "requested", "expected"),
    [
        ("gpt-image-1", (1080, 1920), "1024x1536"),
        ("gpt-image-1.5", (1200, 628), "1536x1024"),
        ("gpt-image-1-mini", (1080, 1080), "1024x1024"),
    ],
)
def test_fixed_size_models_pick_closest_aspect_ratio(model, requested, expected):
    plan = plan_size(model, *requested)
    assert plan.size_param == expected
    assert not plan.native
    assert "gpt-image-2" in plan.warnings[0]


def test_dalle2_uses_its_own_size_set():
    plan = plan_size("dall-e-2", 1080, 1080)
    assert plan.size_param == "1024x1024"
