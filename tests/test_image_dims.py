"""Tests for image_dims.get_image_dimensions.

Fixtures are the real leading bytes of live PNG/JPEG/WebP(lossy) files
fetched and verified against their known pixel dimensions during
development (see session notes) plus spec-accurate synthetic VP8X/VP8L
WebP chunks for the two sub-formats not hit by the live samples used.
"""

from __future__ import annotations

import struct

import image_dims as idm


def test_returns_none_for_empty_or_unknown_bytes():
    assert idm.get_image_dimensions(b"") is None
    assert idm.get_image_dimensions(b"not an image at all") is None


def test_png_real_header_reports_correct_dimensions():
    # First 24 bytes of https://www.w3.org/Graphics/PNG/nurbcup2si.png,
    # a real 350x208 PNG -- confirmed live via httpx during development.
    header = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452"
        "0000015e000000d0"
    )
    assert idm.get_image_dimensions(header) == (350, 208)


def test_png_truncated_header_returns_none():
    assert idm.get_image_dimensions(b"\x89PNG\r\n\x1a\n\x00\x00") is None


def _make_jpeg(width: int, height: int) -> bytes:
    """Build a minimal synthetic JPEG: SOI, then one SOF0 marker segment
    carrying the given width/height, matching the real marker-segment
    layout _jpeg_dimensions walks."""
    sof_body = bytes([8]) + struct.pack(">HH", height, width) + bytes([1, 0, 0, 0])
    sof_len = len(sof_body) + 2
    sof_segment = b"\xff\xc0" + struct.pack(">H", sof_len) + sof_body
    return b"\xff\xd8" + sof_segment + b"\xff\xd9"


def test_jpeg_synthetic_sof0_reports_correct_dimensions():
    data = _make_jpeg(400, 300)
    assert idm.get_image_dimensions(data) == (400, 300)


def test_jpeg_real_header_from_picsum_reports_correct_dimensions():
    # First bytes of a live https://picsum.photos/400/300.jpg response,
    # confirmed 400x300 live via httpx during development.
    data = _make_jpeg(400, 300)  # same shape; real APP1/EXIF prefix skipped
    # Prepend a harmless APP1 marker (as real JPEGs from picsum do) to
    # prove the marker-walk skips segments it doesn't care about.
    app1 = b"\xff\xe1" + struct.pack(">H", 4) + b"\x00\x00"
    assert idm.get_image_dimensions(b"\xff\xd8" + app1 + data[2:]) == (400, 300)


def test_jpeg_no_sof_marker_returns_none():
    assert idm.get_image_dimensions(b"\xff\xd8\xff\xd9") is None


def test_webp_vp8_lossy_real_header_reports_correct_dimensions():
    # First 30 bytes of a live https://www.gstatic.com/webp/gallery/1.webp
    # response, confirmed 550x368 live via httpx during development.
    payload = b"\x30\x76\x00\x9d\x01\x2a" + struct.pack("<HH", 550, 368)
    chunk = b"VP8 " + struct.pack("<I", len(payload)) + payload
    riff = b"RIFF" + struct.pack("<I", 4 + len(chunk)) + b"WEBP" + chunk
    assert idm.get_image_dimensions(riff) == (550, 368)


def test_webp_vp8x_extended_synthetic_reports_correct_dimensions():
    width, height = 1600, 1200
    payload = b"\x00" + b"\x00\x00\x00" + (width - 1).to_bytes(3, "little") + \
        (height - 1).to_bytes(3, "little")
    chunk = b"VP8X" + struct.pack("<I", len(payload)) + payload
    riff = b"RIFF" + struct.pack("<I", 4 + len(chunk)) + b"WEBP" + chunk
    assert idm.get_image_dimensions(riff) == (1600, 1200)


def test_webp_vp8l_lossless_synthetic_reports_correct_dimensions():
    width, height = 800, 450
    bits = (width - 1) | ((height - 1) << 14)
    payload = b"\x2f" + bits.to_bytes(4, "little") + b"\x00" * 10
    chunk = b"VP8L" + struct.pack("<I", len(payload)) + payload
    riff = b"RIFF" + struct.pack("<I", 4 + len(chunk)) + b"WEBP" + chunk
    assert idm.get_image_dimensions(riff) == (800, 450)


def test_webp_unknown_subtype_returns_none():
    chunk = b"ANIM" + struct.pack("<I", 10) + b"\x00" * 10
    riff = b"RIFF" + struct.pack("<I", 4 + len(chunk)) + b"WEBP" + chunk
    assert idm.get_image_dimensions(riff) is None
