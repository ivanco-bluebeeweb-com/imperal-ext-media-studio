"""UX contract tests for the Media Hub image card."""

from __future__ import annotations

import panels


def test_asset_card_labels_original_upscaled_and_metadata_clearly():
    """A non-technical editor must be able to distinguish both files and
    understand every editable metadata field without guessing from a blank
    input placeholder."""
    card = panels._asset_card("pkg-1", {
        "role": "featured",
        "status": "ready",
        "filename": "heat-pump-guide-featured",
        "prompt": "A modern heat pump installation in a bright home.",
        "alt_text": "Modern heat pump beside a home",
        "caption": "Efficient home heating",
        "image_url": "https://cdn.example/upscaled.png?token=fresh",
        "original_image_url": "https://cdn.example/original.png?token=fresh",
        "original_dimensions": "1024 × 768 px",
        "original_format": "PNG",
        "original_file_size": "512.0 KB",
        "upscaled_image_url": "https://cdn.example/upscaled.png?token=fresh",
        "upscaled_dimensions": "2048 × 1536 px",
        "upscaled_format": "PNG",
        "upscaled_file_size": "2.0 MB",
    })

    rendered = repr(card)
    for label in (
        "Original image",
        "1024 × 768 px",
        "Upscaled image",
        "2048 × 1536 px",
        "512.0 KB",
        "2.0 MB",
        "Upscaling",
        "Metadata",
        "Regenerate",
        "Increase size",
        "Generate Upscale",
        "2x",
        "4x",
        "8x",
        "16x",
        "Image title",
        "heat-pump-guide-featured · PNG",
        "Alt text",
        "Caption",
        "Image description",
    ):
        assert label in rendered
    assert card.props["title"] == "Featured Image"
    assert "ready" in rendered.lower()
    assert rendered.index("Upscaling") < rendered.index("Metadata") < rendered.index("Regenerate")
    # Both URLs must remain present: the original is not silently discarded.
    assert "original.png" in rendered
    assert "upscaled.png" in rendered
