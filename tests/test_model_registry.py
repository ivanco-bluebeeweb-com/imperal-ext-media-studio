"""Tests for the multi-provider model registry and the automatic model picker."""

import model_registry as mr


def test_is_known_model_accepts_empty_and_registered():
    assert mr.is_known_model("")
    assert mr.is_known_model("mystic")
    assert mr.is_known_model("imagen4-fast")
    assert mr.is_known_model("imagen4-ultra")
    assert mr.is_known_model("gemini-2.5-flash")


def test_is_known_model_rejects_unregistered():
    assert not mr.is_known_model("gpt-image-1-5")
    assert not mr.is_known_model("not-a-model")


def test_get_model_defaults_to_mystic():
    assert mr.get_model("").id == "mystic"
    assert mr.get_model("bogus-id-not-in-registry") is mr.MODELS["mystic"]


def test_get_model_returns_the_right_spec():
    spec = mr.get_model("imagen4-ultra")
    assert spec.id == "imagen4-ultra"
    assert spec.create_path == "/v1/ai/text-to-image/imagen4-ultra"
    assert spec.status_path == "/v1/ai/text-to-image/imagen4-ultra/{task_id}"


def test_every_model_spec_builds_a_body_with_the_prompt():
    for model_id, spec in mr.MODELS.items():
        body = spec.build_body("a cat on a roof")
        assert body["prompt"] == "a cat on a roof"


# --------------------------- 4:3 landscape aspect ratio ---------------------------

def test_mystic_body_requests_classic_4_3():
    body = mr.MODELS["mystic"].build_body("a warehouse")
    assert body["aspect_ratio"] == "classic_4_3"


def test_imagen4_fast_body_requests_classic_4_3():
    body = mr.MODELS["imagen4-fast"].build_body("a warehouse")
    assert body["aspect_ratio"] == "classic_4_3"


def test_imagen4_ultra_body_requests_classic_4_3():
    body = mr.MODELS["imagen4-ultra"].build_body("a warehouse")
    assert body["aspect_ratio"] == "classic_4_3"


def test_gemini_body_has_no_aspect_ratio_field_documented_exception():
    body = mr.MODELS["gemini-2.5-flash"].build_body("a warehouse")
    assert "aspect_ratio" not in body


# --------------------------- pick_model (auto) ---------------------------

def test_pick_model_illustrative_cue_prefers_mystic():
    assert mr.pick_model("featured", "a clean diagram of airflow", "") == "mystic"
    assert mr.pick_model("inline_1", "a simple icon", "infographic style") == "mystic"


def test_pick_model_portrait_cue_prefers_gemini():
    assert mr.pick_model("inline_1", "a portrait of a happy customer", "") == "gemini-2.5-flash"
    assert mr.pick_model("featured", "our team of workers on site", "") == "gemini-2.5-flash"


def test_pick_model_featured_role_defaults_to_imagen_ultra():
    """A featured hero image with no special cues gets the higher tier."""
    assert mr.pick_model("featured", "a modern office building", "") == "imagen4-ultra"


def test_pick_model_inline_photoreal_uses_imagen_fast():
    assert mr.pick_model("inline_1", "a realistic product shot of a fan unit", "") == "imagen4-fast"


def test_pick_model_inline_with_no_cues_falls_back_to_mystic():
    assert mr.pick_model("inline_2", "something generic", "") == "mystic"


def test_pick_model_always_returns_a_registered_model():
    prompts = [
        ("featured", "a photo of a ventilation system", ""),
        ("inline_1", "an illustration of ductwork", ""),
        ("inline_2", "a portrait of an engineer", "industrial palette"),
        ("featured", "", ""),
    ]
    for role, prompt, style in prompts:
        picked = mr.pick_model(role, prompt, style)
        assert picked in mr.MODELS
