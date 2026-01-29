"""Color utilities for deterministic color assignment."""

from __future__ import annotations

import hashlib
import re

# Material Design 600 Series - optimized for text contrast on white/light backgrounds
MATERIAL_600_PALETTE: tuple[str, ...] = (
    "#E53935",  # Red 600
    "#D81B60",  # Pink 600
    "#8E24AA",  # Purple 600
    "#5E35B1",  # Deep Purple 600
    "#3949AB",  # Indigo 600
    "#1E88E5",  # Blue 600
    "#039BE5",  # Light Blue 600
    "#00ACC1",  # Cyan 600
    "#00897B",  # Teal 600
    "#43A047",  # Green 600
    "#7CB342",  # Light Green 600
    "#C0CA33",  # Lime 600
    "#FDD835",  # Yellow 600
    "#FFB300",  # Amber 600
    "#FB8C00",  # Orange 600
    "#F4511E",  # Deep Orange 600
    "#6D4C41",  # Brown 600
    "#757575",  # Grey 600
    "#546E7A",  # Blue Grey 600
)

_HEX_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


def is_valid_hex_color(value: str | None) -> bool:
    """Check if value is a valid #RRGGBB hex color."""
    if not value:
        return False
    return bool(_HEX_COLOR_RE.match(value))


def pick_material_600_color(key: str | None) -> str:
    """
    Deterministically pick a color from Material 600 palette based on key.

    Same key always returns the same color (stable hash mapping).
    """
    raw = (key or "").strip().lower()
    if not raw:
        return MATERIAL_600_PALETTE[0]
    digest = hashlib.sha256(raw.encode("utf-8")).digest()
    idx = int.from_bytes(digest[:4], "big") % len(MATERIAL_600_PALETTE)
    return MATERIAL_600_PALETTE[idx]
