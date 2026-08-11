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


def test_get_model_defaults_to_third_party_imagen():
    assert mr.get_model("").id == "imagen4-ultra"
    assert mr.get_model("bogus-id-not-in-registry") is mr.MODELS["imagen4-ultra"]


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

def test_pick_model_illustrative_cue_uses_third_party_model():
    assert mr.pick_model("featured", "a clean diagram of airflow", "") == "imagen4-ultra"
    assert mr.pick_model("inline_1", "a simple icon", "infographic style") == "imagen4-fast"


def test_pick_model_portrait_cue_prefers_gemini():
    assert mr.pick_model("inline_1", "a portrait of a happy customer", "") == "gemini-2.5-flash"
    assert mr.pick_model("featured", "our team of workers on site", "") == "gemini-2.5-flash"


def test_pick_model_featured_role_defaults_to_imagen_ultra():
    """A featured hero image with no special cues gets the higher tier."""
    assert mr.pick_model("featured", "a modern office building", "") == "imagen4-ultra"


def test_pick_model_inline_photoreal_uses_imagen_fast():
    assert mr.pick_model("inline_1", "a realistic product shot of a fan unit", "") == "imagen4-fast"


def test_pick_model_inline_with_no_cues_uses_third_party_imagen_fast():
    assert mr.pick_model("inline_2", "something generic", "") == "imagen4-fast"


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


# ------------------- 2026-08-11 expansion: 14 new models -------------------
# One test per model confirms the exact path AND the field shape actually
# read off that model's own docs.magnific.com page -- not a guess. Grouped
# by the aspect-ratio "family" each one belongs to (see model_registry.py's
# module docstring), so a wrong constant would show up as a wrong value here.

def test_nano_banana_pro_confirmed_path_and_colon_aspect_ratio():
    spec = mr.MODELS["nano-banana-pro"]
    assert spec.create_path == "/v1/ai/text-to-image/nano-banana-pro"
    assert spec.status_path == "/v1/ai/text-to-image/nano-banana-pro/{task_id}"
    body = spec.build_body("a warehouse")
    assert body["aspect_ratio"] == "4:3"  # colon family, NOT classic_4_3


def test_nano_banana_pro_flash_confirmed_path_and_colon_aspect_ratio():
    spec = mr.MODELS["nano-banana-pro-flash"]
    assert spec.create_path == "/v1/ai/text-to-image/nano-banana-pro-flash"
    body = spec.build_body("a warehouse")
    assert body["aspect_ratio"] == "4:3"


def test_flux_dev_confirmed_path_and_square_1_1_family():
    spec = mr.MODELS["flux-dev"]
    assert spec.create_path == "/v1/ai/text-to-image/flux-dev"
    assert spec.build_body("x")["aspect_ratio"] == "classic_4_3"


def test_flux_pro_v1_1_confirmed_path():
    spec = mr.MODELS["flux-pro-v1.1"]
    assert spec.create_path == "/v1/ai/text-to-image/flux-pro-v1-1"
    assert spec.build_body("x")["aspect_ratio"] == "classic_4_3"


def test_flux_2_pro_and_turbo_use_pixel_width_height_not_aspect_ratio():
    for model_id in ("flux-2-pro", "flux-2-turbo"):
        body = mr.MODELS[model_id].build_body("x")
        assert "aspect_ratio" not in body
        assert body["width"] == 1024
        assert body["height"] == 768


def test_flux_2_flex_confirmed_path_and_pixel_dimensions():
    spec = mr.MODELS["flux-2-flex"]
    assert spec.create_path == "/v1/ai/text-to-image/flux-2-flex"
    body = spec.build_body("x")
    assert body["width"] == 1024 and body["height"] == 768


def test_flux_2_klein_confirmed_path_and_square_1_1_family():
    spec = mr.MODELS["flux-2-klein"]
    assert spec.create_path == "/v1/ai/text-to-image/flux-2-klein"
    assert spec.build_body("x")["aspect_ratio"] == "classic_4_3"


def test_hyperflux_confirmed_path():
    spec = mr.MODELS["hyperflux"]
    assert spec.create_path == "/v1/ai/text-to-image/hyperflux"
    assert spec.build_body("x")["aspect_ratio"] == "classic_4_3"


def test_z_image_confirmed_path_and_named_image_size():
    spec = mr.MODELS["z-image"]
    assert spec.create_path == "/v1/ai/text-to-image/z-image"
    assert spec.build_body("x")["image_size"] == "landscape_4_3"


def test_seedream_family_confirmed_paths_and_square_1_1_family():
    expected_paths = {
        "seedream-4": "/v1/ai/text-to-image/seedream-v4",
        "seedream-4.5": "/v1/ai/text-to-image/seedream-v4-5",
        "seedream-v5-lite": "/v1/ai/text-to-image/seedream-v5-lite",
        "seedream-v5-pro": "/v1/ai/text-to-image/seedream-v5-pro",
    }
    for model_id, path in expected_paths.items():
        spec = mr.MODELS[model_id]
        assert spec.create_path == path, f"{model_id} path mismatch"
        assert spec.build_body("x")["aspect_ratio"] == "classic_4_3"


def test_all_new_models_are_known_and_valid_choices():
    new_ids = [
        "nano-banana-pro", "nano-banana-pro-flash", "flux-dev",
        "flux-pro-v1.1", "flux-2-pro", "flux-2-turbo", "flux-2-flex",
        "flux-2-klein", "hyperflux", "z-image", "seedream-4",
        "seedream-4.5", "seedream-v5-lite", "seedream-v5-pro",
    ]
    for model_id in new_ids:
        assert mr.is_known_model(model_id), f"{model_id} not registered"


def test_imagen3_deliberately_excluded_as_deprecated():
    """Confirmed on docs.magnific.com: Imagen 3's own page says the
    endpoint is deprecated and scheduled for removal -- must stay OUT."""
    assert "imagen3" not in mr.MODELS


def test_nano_banana_2_deliberately_excluded_no_confirmed_endpoint():
    """The user's own web app shows \"Nano Banana 2\", but no
    docs.magnific.com page for it exists (sitemap-checked) -- only
    nano-banana-pro/-flash are confirmed, so \"nano-banana-2\" must stay
    unregistered rather than guessed."""
    assert "nano-banana-2" not in mr.MODELS
