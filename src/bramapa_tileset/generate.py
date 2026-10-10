"""Deterministic placeholder atlas for the ``bramapa-base`` tileset (Fase 0/1).

Everything here is procedural pixel art at 1x scale: no antialiasing, no RNG.
The layout (which cell each tile lands on) is read from ``tiles.toml``; this
module only knows how to *draw* a tile given its name and frame index, so the
TOML stays the single source of truth for geometry.

Cell coordinate conventions (cell is 64x64, tile diamond is 64x32):

    (0,0) ----------------------- (63,0)
      |                              |
      |      objects grow up here    |
      |                              |
      |           /\\  y=32           |   diamond top corner  (32, 32)
      |          /  \\                |   diamond center      (32, 48)
      |   y=48  <    >               |   left/right corners  (0,48) (63,48)
      |          \\  /                |   diamond bottom      (32, 63)
    (0,63) -------\\/--------------- (63,63)    anchor = bottom-center (32, 64)

Isometric axes on screen: +x of the grid goes down-right with slope 1/2,
+y of the grid goes down-left with slope -1/2 (see spec/README.md).
"""

from __future__ import annotations

import io
import re
from pathlib import Path
from typing import Callable

import numpy as np
from PIL import Image

from .tileset import Tile, Tileset, find_tiles_toml, load_tileset

RGBA = tuple[int, int, int, int]
Canvas = np.ndarray  # (cell_h, cell_w, 4) uint8 view into the atlas


def rgb(r: int, g: int, b: int, a: int = 255) -> RGBA:
    return (r, g, b, a)


# ----------------------------------------------------------------------------
# Palette (muted, RCT-ish). Each terrain has a base and a slightly darker alt
# for the 2-tone dither.
# ----------------------------------------------------------------------------

TERRAIN_PALETTE: dict[str, tuple[RGBA, RGBA]] = {
    "campo": (rgb(122, 162, 84), rgb(112, 150, 76)),
    "pastagem": (rgb(150, 176, 92), rgb(140, 164, 84)),
    "floresta": (rgb(44, 98, 48), rgb(36, 86, 42)),
    "savana": (rgb(164, 172, 74), rgb(152, 160, 66)),
    "caatinga": (rgb(194, 164, 104), rgb(180, 150, 94)),
    "pantanal": (rgb(64, 142, 132), rgb(54, 128, 120)),
    "agua": (rgb(62, 122, 202), rgb(54, 112, 190)),
    "oceano": (rgb(32, 72, 152), rgb(26, 62, 138)),
    "areia": (rgb(226, 206, 152), rgb(214, 194, 140)),
    "urbano": (rgb(142, 142, 148), rgb(130, 130, 136)),
    "montanha": (rgb(122, 102, 92), rgb(106, 90, 80)),
    "agricultura": (rgb(132, 136, 52), rgb(120, 124, 46)),
}

WATER_HIGHLIGHT = {"agua": rgb(126, 176, 232), "oceano": rgb(74, 116, 190), "pantanal": rgb(110, 180, 170)}
# Shoreline foam for the autotiling variants: (bright crest, softer trough).
SHORE_FOAM = {
    "agua": (rgb(214, 236, 250), rgb(170, 210, 242)),
    "oceano": (rgb(176, 206, 240), rgb(118, 160, 216)),
}

C_TRANSPARENT = rgb(0, 0, 0, 0)
C_ROAD = rgb(92, 92, 98)
C_ROAD_EDGE = rgb(70, 70, 76)
C_ROAD_LINE = rgb(222, 214, 150)
C_RIVER = rgb(72, 132, 212)
C_RIVER_EDGE = rgb(54, 108, 186)
C_RIVER_HI = rgb(140, 190, 240)
C_RAIL = rgb(58, 58, 64)
C_TIE = rgb(112, 82, 52)
C_BALLAST = rgb(150, 142, 128)
C_BORDER_ESTADO = rgb(236, 124, 40)
C_BORDER_MUNICIPIO = rgb(244, 244, 244)
C_BORDER_PAIS = rgb(204, 40, 64)
C_SHADOW = rgb(0, 0, 0, 72)
C_TRUNK = rgb(94, 62, 36)
C_TRUNK_DARK = rgb(70, 46, 26)
C_CANOPY = rgb(52, 124, 58)
C_CANOPY_DARK = rgb(34, 90, 42)
C_CANOPY_LIGHT = rgb(96, 160, 86)
C_WALL_L = rgb(150, 150, 162)
C_WALL_R = rgb(186, 186, 196)
C_WALL_EDGE = rgb(96, 96, 108)
C_ROOF = rgb(120, 120, 132)
C_WINDOW = rgb(234, 226, 150)
C_WINDOW_OFF = rgb(80, 96, 120)
C_HOUSE_L = rgb(206, 190, 150)
C_HOUSE_R = rgb(238, 222, 182)
C_HOUSE_EDGE = rgb(120, 104, 76)
C_HOUSE_ROOF = rgb(178, 82, 56)
C_HOUSE_ROOF_HI = rgb(204, 104, 72)
C_DOOR = rgb(92, 62, 40)


# ----------------------------------------------------------------------------
# Geometry helpers (all return boolean masks over the cell grid)
# ----------------------------------------------------------------------------


def grid(cw: int, ch: int) -> tuple[np.ndarray, np.ndarray]:
    ys, xs = np.mgrid[0:ch, 0:cw]
    return xs, ys


def iso_diamond(xs: np.ndarray, ys: np.ndarray, cx: int, top: int, half_w: int, half_h: int) -> np.ndarray:
    """Crisp 2:1 pixel diamond: rows ``top .. top+2*half_h``, widest row is ``2*half_w``.

    Row ``r`` (from the top) has half-width ``(half_w/half_h) * (r+1)`` on the
    upper half and ``(half_w/half_h) * (2*half_h - r)`` on the lower half, which
    for 64x32 gives the classic 4-px top row and 64-px middle rows.
    """
    r = ys - top
    ratio = half_w // half_h
    hw = np.where(r < half_h, ratio * (r + 1), ratio * (2 * half_h - r))
    return (r >= 0) & (r < 2 * half_h) & (xs >= cx - hw) & (xs < cx + hw)


def ground_diamond(xs: np.ndarray, ys: np.ndarray, ts: Tileset) -> np.ndarray:
    """The 64x32 terrain diamond glued to the bottom of the cell."""
    return iso_diamond(
        xs, ys, ts.cell_width // 2, ts.cell_height - ts.tile_height, ts.tile_width // 2, ts.tile_height // 2
    )


def hash_noise(xs: np.ndarray, ys: np.ndarray, seed: int) -> np.ndarray:
    """Deterministic integer hash in 0..255 per pixel. No RNG, so no numpy-version drift."""
    x = xs.astype(np.uint32)
    y = ys.astype(np.uint32)
    s = np.uint32(seed & 0xFFFFFFFF)
    with np.errstate(over="ignore"):
        h = (x * np.uint32(0x9E3779B1)) ^ (y * np.uint32(0x85EBCA77)) ^ (s * np.uint32(0xC2B2AE3D))
        h ^= h >> np.uint32(15)
        h = h * np.uint32(0x2C1B3C6D)
        h ^= h >> np.uint32(12)
        h = h * np.uint32(0x297A2D39)
        h ^= h >> np.uint32(15)
    return (h & np.uint32(0xFF)).astype(np.int32)


def paint(cell: Canvas, mask: np.ndarray, color: RGBA) -> None:
    cell[mask] = color


def blend(cell: Canvas, mask: np.ndarray, color: RGBA) -> None:
    """Straight-alpha 'over' blend for the translucent shadow footprints."""
    a = color[3] / 255.0
    dst = cell[mask].astype(np.float64)
    src = np.array(color[:3], dtype=np.float64)
    dst_a = dst[:, 3:4] / 255.0
    out_a = a + dst_a * (1 - a)
    out_rgb = (src * a + dst[:, :3] * dst_a * (1 - a)) / np.maximum(out_a, 1e-6)
    cell[mask] = np.concatenate([np.rint(out_rgb), np.rint(out_a * 255)], axis=1).astype(np.uint8)


def iso_band(xs: np.ndarray, ys: np.ndarray, cx: int, cy: int, half_thickness: int) -> np.ndarray:
    """Band along the isometric +x axis (screen slope 1/2) through (cx, cy).

    ``half_thickness`` is measured vertically in pixels; the band enters the
    cell at the middle of the NW edge and leaves at the middle of the SE edge,
    so consecutive cells along grid-x join seamlessly.
    """
    d2 = 2 * (ys - cy) - (xs - cx)  # 2 * vertical distance to the axis line
    return np.abs(d2) <= 2 * half_thickness


def band_distance(xs: np.ndarray, ys: np.ndarray, cx: int, cy: int) -> np.ndarray:
    return 2 * (ys - cy) - (xs - cx)


def column_extents(mask: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per-column (has_any, top_row, bottom_row) of a mask."""
    has = mask.any(axis=0)
    top = np.argmax(mask, axis=0)
    bottom = mask.shape[0] - 1 - np.argmax(mask[::-1], axis=0)
    return has, top, bottom


# ----------------------------------------------------------------------------
# Terrain
# ----------------------------------------------------------------------------


def draw_terrain(cell: Canvas, ts: Tileset, tile: Tile, frame: int) -> None:
    xs, ys = grid(ts.cell_width, ts.cell_height)
    dia = ground_diamond(xs, ys, ts)
    base, alt = TERRAIN_PALETTE[tile.name]
    noise = hash_noise(xs, ys, tile.id)

    paint(cell, dia, base)
    paint(cell, dia & (noise < 72), alt)

    cx, cy = ts.cell_width // 2, ts.cell_height - ts.tile_height // 2

    if tile.name in ("agua", "oceano"):
        # Short horizontal dashes on every 4th row, staggered, scrolling with the frame.
        period = 16
        phase = (frame * period) // max(tile.frames, 1)
        dash_len = 6 if tile.name == "agua" else 4
        rows = (ys % 4) == 0
        dashes = ((xs + phase + (ys // 4) * 5) % period) < dash_len
        paint(cell, dia & rows & dashes, WATER_HIGHLIGHT[tile.name])
    elif tile.name == "pantanal":
        # Still water: sparse static glints plus darker reed tufts.
        glints = ((ys % 6) == 0) & (((xs + (ys // 6) * 7) % 12) < 3)
        paint(cell, dia & glints, WATER_HIGHLIGHT["pantanal"])
        tufts = (noise > 236)
        paint(cell, dia & tufts, rgb(40, 96, 72))
    elif tile.name == "agricultura":
        # Plough furrows along the iso +y axis (lines of constant x + 2y).
        furrows = ((xs + 2 * ys) // 6) % 2 == 0
        paint(cell, dia & furrows & (noise < 200), rgb(146, 148, 62))
    elif tile.name == "urbano":
        streets = (((xs + 2 * ys) % 16) < 2) | (((xs - 2 * ys) % 16) < 2)
        paint(cell, dia & streets, rgb(112, 112, 118))
        roofs = (noise > 232)
        paint(cell, dia & roofs, rgb(170, 150, 140))
    elif tile.name == "montanha":
        cap = iso_diamond(xs, ys, cx, cy - 12, 10, 5)
        paint(cell, dia & cap, rgb(236, 236, 242))
        paint(cell, dia & cap & (noise < 56), rgb(206, 208, 216))
        ridge = (xs == cx) | (xs == cx - 1)
        paint(cell, dia & ridge & (ys > cy - 2), rgb(92, 78, 70))
    elif tile.name == "floresta":
        canopies = (noise > 226)
        paint(cell, dia & canopies, rgb(62, 118, 60))
    elif tile.name == "areia":
        ripples = ((ys % 5) == 0) & (((xs + (ys // 5) * 3) % 9) < 4)
        paint(cell, dia & ripples & (noise < 160), rgb(236, 220, 170))
    elif tile.name == "caatinga":
        shrubs = (noise > 238)
        paint(cell, dia & shrubs, rgb(120, 110, 70))


# ----------------------------------------------------------------------------
# Shoreline autotiling variants (agua / oceano)
# ----------------------------------------------------------------------------
#
# Neighbour mask (spec v0): N = 1 (x, y-1), E = 2 (x+1, y), S = 4 (x, y+1),
# W = 8 (x-1, y); a bit is SET when that neighbour has the same cover, so a
# CLEAR bit means "shore on that side" and gets a foam line.
#
# Which diamond edge does each neighbour touch? Derive it from the projection
# rather than from compass intuition. Screen position of grid cell (x, y):
#
#     sx = (x - y) * 32        sy = (x + y) * 16
#
#     neighbour      grid delta   screen delta   lies to the   touches edge
#     N  (x, y-1)    (0, -1)      (+32, -16)     upper-right   NE  (upper-right)
#     E  (x+1, y)    (+1, 0)      (+32, +16)     lower-right   SE  (lower-right)
#     S  (x, y+1)    (0, +1)      (-32, +16)     lower-left    SW  (lower-left)
#     W  (x-1, y)    (-1, 0)      (-32, -16)     upper-left    NW  (upper-left)
#
# So in cell pixels (diamond centre at (cx, cy)): bit N -> upper & right half,
# bit E -> lower & right, bit S -> lower & left, bit W -> upper & left.

MASK_N, MASK_E, MASK_S, MASK_W = 1, 2, 4, 8
SHORE_THICKNESS = 2  # rows; a 2:1 edge then covers 4 px per row

_VARIANT_RE = re.compile(r"^(?P<base>agua|oceano)_m(?P<mask>\d{1,2})$")


def shore_variant(tile: Tile) -> tuple[str, int] | None:
    """``agua_m05`` -> ("agua", 5); ``None`` for every other tile name."""
    m = _VARIANT_RE.match(tile.name)
    if m is None:
        return None
    mask = int(m.group("mask"))
    if not 0 <= mask <= 15:
        raise ValueError(f"tile {tile.id} ({tile.name}): mask {mask} out of range 0..15")
    return m.group("base"), mask


def shore_edges(xs: np.ndarray, ys: np.ndarray, ts: Tileset) -> dict[int, np.ndarray]:
    """Foam band for each neighbour bit, keyed by the bit (MASK_N, ...)."""
    cx, cy = ts.cell_width // 2, ts.cell_height - ts.tile_height // 2
    top = ts.cell_height - ts.tile_height
    half_w, half_h = ts.tile_width // 2, ts.tile_height // 2
    t = SHORE_THICKNESS
    ratio = half_w // half_h
    outer = iso_diamond(xs, ys, cx, top, half_w, half_h)
    inner = iso_diamond(xs, ys, cx, top + t, half_w - ratio * t, half_h - t)
    rim = outer & ~inner
    upper, right = ys < cy, xs >= cx
    return {
        MASK_N: rim & upper & right,  # NE edge
        MASK_E: rim & ~upper & right,  # SE edge
        MASK_S: rim & ~upper & ~right,  # SW edge
        MASK_W: rim & upper & ~right,  # NW edge
    }


def draw_shore(cell: Canvas, ts: Tileset, base_name: str, mask: int, frame: int, frames: int) -> None:
    """Foam line on every diamond edge whose neighbour bit is CLEAR in ``mask``."""
    xs, ys = grid(ts.cell_width, ts.cell_height)
    crest, trough = SHORE_FOAM[base_name]
    # Crest/trough alternate along the edge and drift with the frame, like the water dashes.
    period = 8
    phase = (frame * period) // max(frames, 1)
    along = ((xs + 2 * ys + phase) // 2) % period  # position along a 2:1 edge
    for bit, band in shore_edges(xs, ys, ts).items():
        if mask & bit:
            continue
        paint(cell, band, trough)
        paint(cell, band & (along < 5), crest)


def draw_terrain_or_shore(cell: Canvas, ts: Tileset, tile: Tile, frame: int) -> None:
    variant = shore_variant(tile)
    if variant is None:
        draw_terrain(cell, ts, tile, frame)
        return
    base_name, mask = variant
    base = ts.by_name().get(base_name)
    if base is None:
        raise ValueError(f"tile {tile.id} ({tile.name}): base tile {base_name!r} not declared")
    # Same pixels as the base tile (same noise seed, same frame phase), then foam on top.
    draw_terrain(cell, ts, base, frame)
    draw_shore(cell, ts, base_name, mask, frame, base.frames)


# ----------------------------------------------------------------------------
# Overlays
# ----------------------------------------------------------------------------


def draw_overlay(cell: Canvas, ts: Tileset, tile: Tile, frame: int) -> None:
    xs, ys = grid(ts.cell_width, ts.cell_height)
    dia = ground_diamond(xs, ys, ts)
    cx, cy = ts.cell_width // 2, ts.cell_height - ts.tile_height // 2
    d2 = band_distance(xs, ys, cx, cy)

    if tile.name == "rodovia":
        paint(cell, dia & (np.abs(d2) <= 10), C_ROAD_EDGE)
        paint(cell, dia & (np.abs(d2) <= 8), C_ROAD)
        center_dash = (np.abs(d2) <= 1) & (((xs // 6) % 2) == 0)
        paint(cell, dia & center_dash, C_ROAD_LINE)

    elif tile.name == "rio":
        paint(cell, dia & (np.abs(d2) <= 8), C_RIVER_EDGE)
        paint(cell, dia & (np.abs(d2) <= 6), C_RIVER)
        period = 12
        phase = (frame * period) // max(tile.frames, 1)
        ripples = (((xs + phase) % period) < 3) & (np.abs(d2 + 3) <= 1)
        ripples |= (((xs + phase + 6) % period) < 3) & (np.abs(d2 - 3) <= 1)
        paint(cell, dia & ripples, C_RIVER_HI)

    elif tile.name.startswith("limite_"):
        color = {
            "limite_estado": C_BORDER_ESTADO,
            "limite_municipio": C_BORDER_MUNICIPIO,
            "limite_pais": C_BORDER_PAIS,
        }[tile.name]
        inner = iso_diamond(xs, ys, cx, ts.cell_height - ts.tile_height + 2, ts.tile_width // 2 - 4, ts.tile_height // 2 - 2)
        outline = dia & ~inner
        dash_len = {"limite_estado": 6, "limite_municipio": 3, "limite_pais": 8}[tile.name]
        dashes = ((xs // dash_len) % 2) == 0
        paint(cell, outline & dashes, color)

    elif tile.name == "ferrovia":
        paint(cell, dia & (np.abs(d2) <= 9), C_BALLAST)
        ties = ((xs % 6) < 2) & (np.abs(d2) <= 7)
        paint(cell, dia & ties, C_TIE)
        rails = (np.abs(d2 - 4) <= 1) | (np.abs(d2 + 4) <= 1)
        paint(cell, dia & rails, C_RAIL)

    else:
        raise ValueError(f"no placeholder drawing for overlay {tile.name!r}")


# ----------------------------------------------------------------------------
# Objects (anchored bottom-center; footprint centered on the ground diamond)
# ----------------------------------------------------------------------------


def draw_iso_box(
    cell: Canvas,
    xs: np.ndarray,
    ys: np.ndarray,
    cx: int,
    cy: int,
    half_w: int,
    half_h: int,
    height: int,
    left: RGBA,
    right: RGBA,
    top: RGBA,
    edge: RGBA,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Draw a box standing on an iso footprint centered at (cx, cy).

    Returns (left_face, right_face, top_face) masks for decoration.
    """
    foot = iso_diamond(xs, ys, cx, cy - half_h, half_w, half_h)
    has, f_top, f_bottom = column_extents(foot)
    col_top = f_top[None, :]
    col_bottom = f_bottom[None, :]
    in_cols = has[None, :]

    faces = in_cols & (ys <= col_bottom) & (ys > col_bottom - height) & (ys > col_top - height)
    left_face = faces & (xs < cx)
    right_face = faces & (xs >= cx)
    top_face = in_cols & (ys >= col_top - height) & (ys <= col_bottom - height)

    paint(cell, left_face, left)
    paint(cell, right_face, right)
    paint(cell, top_face, top)

    # Edges: vertical corner line, outer silhouette columns, and the roof rim.
    corner = faces & ((xs == cx) | (xs == cx - 1))
    paint(cell, corner, edge)
    silhouette = faces & ((xs == cx - half_w) | (xs == cx + half_w - 1))
    paint(cell, silhouette, edge)
    rim = in_cols & (ys == col_bottom - height)
    paint(cell, rim & faces, edge)
    return left_face, right_face, top_face


def draw_object(cell: Canvas, ts: Tileset, tile: Tile, frame: int) -> None:
    xs, ys = grid(ts.cell_width, ts.cell_height)
    cx, cy = ts.cell_width // 2, ts.cell_height - ts.tile_height // 2  # ground diamond center
    noise = hash_noise(xs, ys, tile.id)

    if tile.name == "arvore":
        shadow = iso_diamond(xs, ys, cx + 2, cy - 4, 12, 6)
        blend(cell, shadow, C_SHADOW)
        trunk = (xs >= cx - 2) & (xs < cx + 2) & (ys >= cy - 14) & (ys <= cy + 1)
        paint(cell, trunk, C_TRUNK)
        paint(cell, trunk & (xs >= cx), C_TRUNK_DARK)
        ccx, ccy, r = cx - 0.5, cy - 24.5, 13.0
        dist2 = (xs - ccx) ** 2 + (ys - ccy) ** 2
        canopy = dist2 <= r * r
        paint(cell, canopy, C_CANOPY)
        paint(cell, canopy & (dist2 > (r - 2.0) ** 2), C_CANOPY_DARK)
        paint(cell, canopy & (dist2 <= (r - 2.0) ** 2) & (noise < 40), C_CANOPY_DARK)
        hi = ((xs - (ccx - 5)) ** 2 + (ys - (ccy - 5)) ** 2) <= 5.0 * 5.0
        paint(cell, hi & (noise < 180), C_CANOPY_LIGHT)
        bottom_shade = canopy & (ys > ccy + 6) & (noise < 128)
        paint(cell, bottom_shade, C_CANOPY_DARK)

    elif tile.name == "predio":
        shadow = iso_diamond(xs, ys, cx + 3, cy - 7, 18, 9)
        blend(cell, shadow, C_SHADOW)
        left, right, top = draw_iso_box(cell, xs, ys, cx, cy, 16, 8, 34, C_WALL_L, C_WALL_R, C_ROOF, C_WALL_EDGE)
        foot = iso_diamond(xs, ys, cx, cy - 8, 16, 8)
        _, _, f_bottom = column_extents(foot)
        up = f_bottom[None, :] - ys  # height above the footprint's lower edge, per column
        cols = ((xs - (cx - 16)) % 6) >= 3
        cols &= ((xs - (cx - 16)) % 6) < 5
        rows = ((up - 4) % 8) < 2
        windows = (left | right) & cols & rows & (up >= 4) & (up < 32)
        windows &= ~((xs == cx) | (xs == cx - 1))
        paint(cell, windows, C_WINDOW)
        paint(cell, windows & (noise < 70), C_WINDOW_OFF)
        # Roof hatch / water tank
        tank = iso_diamond(xs, ys, cx + 2, cy - 8 - 34 - 2, 4, 2)
        paint(cell, tank & top, C_WALL_EDGE)

    elif tile.name == "casa":
        shadow = iso_diamond(xs, ys, cx + 2, cy - 5, 14, 7)
        blend(cell, shadow, C_SHADOW)
        left, right, top = draw_iso_box(cell, xs, ys, cx, cy, 12, 6, 12, C_HOUSE_L, C_HOUSE_R, C_HOUSE_ROOF, C_HOUSE_EDGE)
        # Roof: lighter right half, ridge along the iso +x axis.
        paint(cell, top & (xs >= cx), C_HOUSE_ROOF_HI)
        ridge = top & (np.abs(band_distance(xs, ys, cx, cy - 6 - 12)) <= 1)
        paint(cell, ridge, C_HOUSE_EDGE)
        # Door on the right face, window on the left face.
        foot = iso_diamond(xs, ys, cx, cy - 6, 12, 6)
        _, _, f_bottom = column_extents(foot)
        up = f_bottom[None, :] - ys
        door = right & (xs >= cx + 4) & (xs < cx + 7) & (up >= 0) & (up < 7)
        paint(cell, door, C_DOOR)
        window = left & (xs >= cx - 8) & (xs < cx - 5) & (up >= 4) & (up < 7)
        paint(cell, window, C_WINDOW)

    else:
        raise ValueError(f"no placeholder drawing for object {tile.name!r}")


DRAWERS: dict[str, Callable[[Canvas, Tileset, Tile, int], None]] = {
    "terrain": draw_terrain_or_shore,
    "overlay": draw_overlay,
    "object": draw_object,
}


# ----------------------------------------------------------------------------
# Atlas assembly
# ----------------------------------------------------------------------------


def _pow2_at_least(n: int) -> int:
    p = 1
    while p < n:
        p *= 2
    return p


def atlas_size(ts: Tileset) -> tuple[int, int]:
    """Power-of-two-friendly atlas size that holds every cell declared in tiles.toml."""
    w = _pow2_at_least(max(ts.cols_needed(), 1) * ts.cell_width)
    h = _pow2_at_least(max(ts.rows_needed(), 1) * ts.cell_height)
    return w, h


def render_atlas(ts: Tileset) -> np.ndarray:
    w, h = atlas_size(ts)
    atlas = np.zeros((h, w, 4), dtype=np.uint8)
    cw, ch = ts.cell_width, ts.cell_height
    for tile in ts.tiles:
        if tile.cls not in DRAWERS:
            raise ValueError(f"tile {tile.id} ({tile.name}): unknown class {tile.cls!r}")
        for frame, (col, row) in enumerate(tile.cells()):
            cell = atlas[row * ch : (row + 1) * ch, col * cw : (col + 1) * cw]
            if cell.shape[:2] != (ch, cw):
                raise ValueError(f"tile {tile.id} ({tile.name}) frame {frame} does not fit in the atlas")
            DRAWERS[tile.cls](cell, ts, tile, frame)
    return atlas


def encode_png(pixels: np.ndarray) -> bytes:
    """Encode RGBA pixels as PNG with fixed options so the bytes are reproducible."""
    img = Image.fromarray(pixels, mode="RGBA")
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=False, compress_level=9)
    return buf.getvalue()


def generate(tiles_toml: str | Path | None = None, out: str | Path | None = None) -> Path:
    """Render the placeholder atlas and write it to ``out`` (default: the path tiles.toml declares)."""
    ts = load_tileset(find_tiles_toml(tiles_toml))
    out_path = Path(out) if out is not None else ts.atlas_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(encode_png(render_atlas(ts)))
    return out_path
