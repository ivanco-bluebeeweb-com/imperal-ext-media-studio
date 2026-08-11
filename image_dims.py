"""Read pixel width/height directly from raw image bytes -- no Pillow.

WHY NOT JUST ADD PILLOW TO requirements.txt. Every other Imperal extension in
this workspace (13 checked) declares exactly `imperal-sdk` (+ `pydantic` for
some) and nothing else -- there is no confirmed, documented guarantee that
the deploy pipeline runs `pip install -r requirements.txt` against anything
beyond that pinned pair before running the extension in production. Adding a
new third-party dependency on a guess risks a working-locally-broken-in-prod
split that would be far worse than writing ~60 lines of a well-documented
binary format parser. PNG/JPEG/WebP header layouts are public, stable, and
tiny -- exactly the kind of "confirmed via spec, not guessed" work this
codebase already does for Magnific's API shapes.

Only PNG, JPEG and WebP are handled -- the three formats any of Media Hub's
registered models (Mystic/Imagen4/Gemini/Flux/Seedream/etc., all documented
in model_registry.py) could plausibly return. An unrecognized format returns
None; callers must treat that as "can't verify size" and skip auto-upscale
rather than guess.
"""

from __future__ import annotations

import struct


def get_image_dimensions(data: bytes) -> tuple[int, int] | None:
    """Return (width, height) in pixels, or None if the format isn't one of
    PNG/JPEG/WebP or the header is truncated/malformed."""
    if not data:
        return None
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return _png_dimensions(data)
    if data.startswith(b"\xff\xd8"):
        return _jpeg_dimensions(data)
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return _webp_dimensions(data)
    return None


def _png_dimensions(data: bytes) -> tuple[int, int] | None:
    """PNG: 8-byte signature, then the first chunk is always IHDR --
    4-byte length + b"IHDR" + 4-byte width + 4-byte height (big-endian).
    Confirmed against the PNG spec (w3.org/TR/png/#11IHDR)."""
    if len(data) < 24 or data[12:16] != b"IHDR":
        return None
    width, height = struct.unpack(">II", data[16:24])
    return (width, height) if width and height else None


def _jpeg_dimensions(data: bytes) -> tuple[int, int] | None:
    """JPEG: walk the marker segments looking for a Start-Of-Frame marker
    (0xC0-0xCF, excluding the DHT/JPG/DAC markers 0xC4/0xC8/0xCC which reuse
    that range for other purposes). An SOF segment's body is
    [1-byte precision][2-byte height][2-byte width], big-endian."""
    size = len(data)
    i = 2
    while i + 9 < size:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        if i + 4 > size:
            return None
        seg_len = struct.unpack(">H", data[i + 2:i + 4])[0]
        is_sof = 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC)
        if is_sof:
            if i + 9 > size:
                return None
            height, width = struct.unpack(">HH", data[i + 5:i + 9])
            return (width, height) if width and height else None
        if marker == 0xD9 or seg_len < 2:  # EOI or malformed length
            return None
        i += 2 + seg_len
    return None


def _webp_dimensions(data: bytes) -> tuple[int, int] | None:
    """WebP: RIFF container. Dimensions live in the sub-chunk that follows
    the 12-byte RIFF/size/WEBP header -- format differs by variant, all per
    the documented WebP container spec (developers.google.com/speed/webp/
    docs/riff_container)."""
    if len(data) < 30:
        return None
    tag = data[12:16]
    if tag == b"VP8X":
        # 24-bit little-endian (width-1) and (height-1) at offset 24/27.
        w = (data[24] | (data[25] << 8) | (data[26] << 16)) + 1
        h = (data[27] | (data[28] << 8) | (data[29] << 16)) + 1
        return (w, h)
    if tag == b"VP8 ":
        # Lossy: chunk payload (starting at offset 20) is [3-byte VP8 frame
        # tag][3-byte start code 0x9D 0x01 0x2A][14-bit width][14-bit height]
        # -- the frame tag comes BEFORE the start code, so the start code
        # sits at offset 23, not 20. Confirmed against RFC 6386 sec.9.1.
        if data[23:26] != b"\x9d\x01\x2a":
            return None
        w = struct.unpack("<H", data[26:28])[0] & 0x3FFF
        h = struct.unpack("<H", data[28:30])[0] & 0x3FFF
        return (w, h) if w and h else None
    if tag == b"VP8L":
        # Lossless: signature byte 0x2F, then a 14-bit width-1/height-1 pair
        # packed across the next 4 bytes, little-endian bit order.
        if data[20:21] != b"\x2f":
            return None
        bits = int.from_bytes(data[21:25], "little")
        w = (bits & 0x3FFF) + 1
        h = ((bits >> 14) & 0x3FFF) + 1
        return (w, h)
    return None
