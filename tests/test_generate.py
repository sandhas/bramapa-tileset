"""The committed atlas.png must be exactly what the generator produces."""

from pathlib import Path

from PIL import Image

from bramapa_tileset.generate import atlas_size, generate, render_atlas
from bramapa_tileset.tileset import load_tileset


def test_generate_is_deterministic(repo_root: Path, tmp_path: Path) -> None:
    out = generate(repo_root / "tiles.toml", tmp_path / "atlas.png")
    assert out.read_bytes() == (repo_root / "atlas.png").read_bytes(), (
        "atlas.png is out of date: run `uv run bramapa-tileset generate` and commit the result"
    )


def test_two_renders_are_identical(repo_root: Path) -> None:
    ts = load_tileset(repo_root / "tiles.toml")
    a = render_atlas(ts)
    b = render_atlas(ts)
    assert (a == b).all()


def test_atlas_geometry(repo_root: Path) -> None:
    ts = load_tileset(repo_root / "tiles.toml")
    with Image.open(repo_root / "atlas.png") as img:
        assert img.mode == "RGBA"
        assert img.size == atlas_size(ts)
        w, h = img.size
    assert w % ts.cell_width == 0 and h % ts.cell_height == 0
    # power-of-two friendly
    assert w & (w - 1) == 0 and h & (h - 1) == 0


def test_terrain_diamond_is_flush_with_cell_bottom(repo_root: Path) -> None:
    ts = load_tileset(repo_root / "tiles.toml")
    atlas = render_atlas(ts)
    cw, ch = ts.cell_width, ts.cell_height
    for tile in ts.tiles:
        if tile.cls != "terrain":
            continue
        for col, row in tile.cells():
            alpha = atlas[row * ch : (row + 1) * ch, col * cw : (col + 1) * cw, 3]
            # Nothing above the diamond, full-width rows at the diamond's middle,
            # the 4-px tip on the last row (anchor = bottom-center).
            assert not alpha[: ch - ts.tile_height].any(), tile.name
            assert (alpha[ch - ts.tile_height // 2] == 255).all(), tile.name
            assert (alpha[ch - 1] > 0).sum() == 4, tile.name
            assert alpha[ch - 1, cw // 2 - 2 : cw // 2 + 2].all(), tile.name


def test_animated_frames_differ(repo_root: Path) -> None:
    ts = load_tileset(repo_root / "tiles.toml")
    atlas = render_atlas(ts)
    cw, ch = ts.cell_width, ts.cell_height
    for tile in ts.tiles:
        if not tile.animated:
            continue
        cells = [atlas[r * ch : (r + 1) * ch, c * cw : (c + 1) * cw] for c, r in tile.cells()]
        for i in range(1, len(cells)):
            assert not (cells[0] == cells[i]).all(), f"{tile.name} frame {i} equals frame 0"
