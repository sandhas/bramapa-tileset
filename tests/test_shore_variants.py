"""Fase 1: shoreline autotiling variants of agua (100..115) and oceano (120..135).

Edge <-> neighbour bit mapping, derived from sx = (x - y) * 32, sy = (x + y) * 16:
N (x, y-1) -> upper-right edge, E (x+1, y) -> lower-right, S (x, y+1) -> lower-left,
W (x-1, y) -> upper-left.
"""

from pathlib import Path

import numpy as np
import pytest

from bramapa_tileset.generate import MASK_E, MASK_N, MASK_S, MASK_W, render_atlas, shore_edges, grid
from bramapa_tileset.tileset import Tileset, load_tileset

VARIANTS = {"agua": (100, 2), "oceano": (120, 3)}


@pytest.fixture(scope="module")
def ts(repo_root: Path) -> Tileset:
    return load_tileset(repo_root / "tiles.toml")


@pytest.fixture(scope="module")
def atlas(ts: Tileset) -> np.ndarray:
    return render_atlas(ts)


def cells(atlas: np.ndarray, ts: Tileset, tile_id: int) -> list[np.ndarray]:
    t = ts.by_id()[tile_id]
    cw, ch = ts.cell_width, ts.cell_height
    return [atlas[r * ch : (r + 1) * ch, c * cw : (c + 1) * cw] for c, r in t.cells()]


def test_variants_declared(ts: Tileset) -> None:
    by_id = ts.by_id()
    for cover, (first, base_id) in VARIANTS.items():
        base = by_id[base_id]
        for mask in range(16):
            t = by_id[first + mask]
            assert t.name == f"{cover}_m{mask:02d}"
            assert t.cls == "terrain" and t.cover == cover
            assert t.frames == 4 and t.fps == base.fps
            assert t.row >= 4, "variants live in new rows so ids 1..42 keep their cells"


def test_autotile_rules_cover_all_masks(ts: Tileset) -> None:
    rules = {a.cover: a for a in ts.autotiles}
    for cover, (first, base_id) in VARIANTS.items():
        rule = rules[cover]
        assert rule.default == base_id
        # mask 15 (surrounded by the same cover) is the base tile, per tileset.md
        expected = {m: first + m for m in range(15)} | {15: base_id}
        assert {int(k): v for k, v in rule.masks.items()} == expected
    assert rules["rio"].default == 21 and set(rules["rio"].masks) == {"0", "5", "10", "15"}


def test_mask15_equals_base_tile(atlas: np.ndarray, ts: Tileset) -> None:
    for cover, (first, base_id) in VARIANTS.items():
        for a, b in zip(cells(atlas, ts, first + 15), cells(atlas, ts, base_id), strict=True):
            assert (a == b).all(), cover


def test_mask0_has_foam_on_all_four_edges(atlas: np.ndarray, ts: Tileset) -> None:
    xs, ys = grid(ts.cell_width, ts.cell_height)
    edges = shore_edges(xs, ys, ts)
    for cover, (first, _) in VARIANTS.items():
        m0, m15 = cells(atlas, ts, first)[0], cells(atlas, ts, first + 15)[0]
        for bit, band in edges.items():
            assert (m0[band] != m15[band]).any(axis=1).any(), f"{cover}: no foam on edge bit {bit}"
            assert (m0[band, 3] == 255).all(), "foam must stay inside the diamond"
        # Outside the four edge bands the variant is the plain water.
        rim = np.zeros_like(band)
        for band in edges.values():
            rim |= band
        assert (m0[~rim] == m15[~rim]).all()


@pytest.mark.parametrize(
    ("mask", "foam_bit"),
    [(14, MASK_N), (13, MASK_E), (11, MASK_S), (7, MASK_W)],
    ids=["only-N-clear", "only-E-clear", "only-S-clear", "only-W-clear"],
)
def test_single_clear_bit_foams_only_that_edge(atlas: np.ndarray, ts: Tileset, mask: int, foam_bit: int) -> None:
    xs, ys = grid(ts.cell_width, ts.cell_height)
    edges = shore_edges(xs, ys, ts)
    cx, cy = ts.cell_width // 2, ts.cell_height - ts.tile_height // 2
    expected_side = {
        MASK_N: (ys < cy) & (xs >= cx),  # upper-right
        MASK_E: (ys >= cy) & (xs >= cx),  # lower-right
        MASK_S: (ys >= cy) & (xs < cx),  # lower-left
        MASK_W: (ys < cy) & (xs < cx),  # upper-left
    }[foam_bit]
    assert edges[foam_bit].any() and (expected_side[edges[foam_bit]]).all()
    for cover, (first, _) in VARIANTS.items():
        for frame, (v, base) in enumerate(zip(cells(atlas, ts, first + mask), cells(atlas, ts, first + 15), strict=True)):
            diff = (v != base).any(axis=2)
            assert diff.any(), f"{cover} m{mask} frame {frame}: no foam"
            assert expected_side[diff].all(), f"{cover} m{mask} frame {frame}: foam on the wrong side"
            for bit, band in edges.items():
                if bit != foam_bit:
                    assert not diff[band].any(), f"{cover} m{mask} frame {frame}: foam on edge bit {bit}"


def test_foam_animates(atlas: np.ndarray, ts: Tileset) -> None:
    xs, ys = grid(ts.cell_width, ts.cell_height)
    band = shore_edges(xs, ys, ts)[MASK_N]
    for cover, (first, _) in VARIANTS.items():
        frames = cells(atlas, ts, first)
        assert not (frames[0][band] == frames[1][band]).all(), cover
