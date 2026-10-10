"""Parsing of ``tiles.toml`` (spec v0) into plain dataclasses.

The loader is deliberately tolerant: it only fails on structural problems
(missing file, invalid TOML, a tile without ``id``). Semantic validation
(bounds, duplicates, class names, ...) lives in :mod:`bramapa_tileset.check`
so that a broken file can still be loaded and reported in full.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

VALID_CLASSES = ("terrain", "overlay", "object")
SUPPORTED_SPEC_VERSION = 0


@dataclass(frozen=True)
class Tile:
    id: int
    name: str
    cls: str
    col: int
    row: int
    frames: int = 1
    fps: int = 0
    cover: str | None = None

    @property
    def animated(self) -> bool:
        return self.frames > 1

    def cells(self) -> list[tuple[int, int]]:
        """(col, row) of every cell this tile occupies, frame by frame."""
        return [(self.col + f, self.row) for f in range(self.frames)]


@dataclass(frozen=True)
class Autotile:
    cover: str
    default: int
    masks: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Tileset:
    path: Path
    spec_version: int
    id: str
    version: str
    atlas: str
    tile_width: int
    tile_height: int
    cell_width: int
    cell_height: int
    license: str
    tiles: list[Tile]
    autotiles: list[Autotile]

    @property
    def atlas_path(self) -> Path:
        return self.path.parent / self.atlas

    def by_id(self) -> dict[int, Tile]:
        return {t.id: t for t in self.tiles}

    def by_name(self) -> dict[str, Tile]:
        return {t.name: t for t in self.tiles}

    def cols_needed(self) -> int:
        return max((t.col + t.frames for t in self.tiles), default=0)

    def rows_needed(self) -> int:
        return max((t.row + 1 for t in self.tiles), default=0)


def _int(value: Any, default: int) -> int:
    """Coerce a TOML value to int, keeping obviously wrong types as-is for the checker."""
    if value is None:
        return default
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    return value  # type: ignore[return-value]  # reported by check()


def load_tileset(path: str | Path) -> Tileset:
    path = Path(path)
    with path.open("rb") as fh:
        data = tomllib.load(fh)

    ts = data.get("tileset", {})
    tiles: list[Tile] = []
    for raw in data.get("tiles", []):
        if "id" not in raw:
            raise ValueError(f"{path}: [[tiles]] entry without id: {raw!r}")
        tiles.append(
            Tile(
                id=_int(raw.get("id"), 0),
                name=str(raw.get("name", "")),
                cls=str(raw.get("class", "")),
                col=_int(raw.get("col"), 0),
                row=_int(raw.get("row"), 0),
                frames=_int(raw.get("frames"), 1),
                fps=_int(raw.get("fps"), 0),
                cover=raw.get("cover"),
            )
        )

    autotiles = [
        Autotile(
            cover=str(raw.get("cover", "")),
            default=_int(raw.get("default"), 0),
            masks=dict(raw.get("masks", {})),
        )
        for raw in data.get("autotile", [])
    ]

    return Tileset(
        path=path,
        spec_version=_int(data.get("spec_version"), -1),
        id=str(ts.get("id", "")),
        version=str(ts.get("version", "")),
        atlas=str(ts.get("atlas", "atlas.png")),
        tile_width=_int(ts.get("tile_width"), 0),
        tile_height=_int(ts.get("tile_height"), 0),
        cell_width=_int(ts.get("cell_width"), 0),
        cell_height=_int(ts.get("cell_height"), 0),
        license=str(ts.get("license", "")),
        tiles=tiles,
        autotiles=autotiles,
    )


def find_tiles_toml(start: str | Path | None = None) -> Path:
    """Locate ``tiles.toml``: explicit path, cwd, or the repo root of this package."""
    if start is not None:
        p = Path(start)
        return p / "tiles.toml" if p.is_dir() else p
    cwd = Path.cwd() / "tiles.toml"
    if cwd.is_file():
        return cwd
    repo = Path(__file__).resolve().parents[2] / "tiles.toml"
    if repo.is_file():
        return repo
    raise FileNotFoundError("tiles.toml not found in cwd nor in the package repo root")
