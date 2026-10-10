"""Consistency checks between ``tiles.toml`` and ``atlas.png``."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from .tileset import SUPPORTED_SPEC_VERSION, VALID_CLASSES, Tileset, find_tiles_toml, load_tileset

MAX_TILE_ID = 0xFFFF


@dataclass
class CheckResult:
    tileset: Tileset | None
    atlas_size: tuple[int, int] | None
    problems: list[str] = field(default_factory=list)
    tile_problems: dict[int, list[str]] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.problems

    def add(self, message: str, tile_id: int | None = None) -> None:
        self.problems.append(message)
        if tile_id is not None:
            self.tile_problems.setdefault(tile_id, []).append(message)


def _is_int(v: object) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def check(tiles_toml: str | Path | None = None, atlas: str | Path | None = None) -> CheckResult:
    tiles_path = find_tiles_toml(tiles_toml)
    try:
        ts = load_tileset(tiles_path)
    except Exception as exc:  # noqa: BLE001 - report any load failure as a problem
        result = CheckResult(tileset=None, atlas_size=None)
        result.add(f"cannot load {tiles_path}: {exc}")
        return result

    result = CheckResult(tileset=ts, atlas_size=None)

    # --- header -------------------------------------------------------------
    if ts.spec_version != SUPPORTED_SPEC_VERSION:
        result.add(f"spec_version is {ts.spec_version!r}, expected {SUPPORTED_SPEC_VERSION}")
    if not ts.id:
        result.add("[tileset].id is missing")
    if not ts.version:
        result.add("[tileset].version is missing")
    if not ts.license:
        result.add("[tileset].license is missing")
    for name in ("tile_width", "tile_height", "cell_width", "cell_height"):
        v = getattr(ts, name)
        if not _is_int(v) or v <= 0:
            result.add(f"[tileset].{name} must be a positive integer, got {v!r}")
    geometry_ok = all(_is_int(getattr(ts, n)) and getattr(ts, n) > 0 for n in ("cell_width", "cell_height"))
    if geometry_ok and (ts.tile_width > ts.cell_width or ts.tile_height > ts.cell_height):
        result.add("tile diamond does not fit inside a cell")

    # --- atlas --------------------------------------------------------------
    atlas_path = Path(atlas) if atlas is not None else ts.atlas_path
    aw = ah = None
    if not atlas_path.is_file():
        result.add(f"atlas not found: {atlas_path}")
    else:
        with Image.open(atlas_path) as img:
            aw, ah = img.size
            if img.mode != "RGBA":
                result.add(f"atlas mode is {img.mode}, expected RGBA")
        result.atlas_size = (aw, ah)
        if geometry_ok and (aw % ts.cell_width or ah % ts.cell_height):
            result.add(f"atlas size {aw}x{ah} is not a multiple of the cell size {ts.cell_width}x{ts.cell_height}")

    # --- tiles ---------------------------------------------------------------
    if not ts.tiles:
        result.add("no [[tiles]] declared")

    seen_ids: dict[int, str] = {}
    seen_names: dict[str, int] = {}
    occupied: dict[tuple[int, int], int] = {}
    covers: dict[str, set[int]] = {}

    for t in ts.tiles:
        tid = t.id
        if not _is_int(tid) or tid < 0 or tid > MAX_TILE_ID:
            result.add(f"tile {tid!r} ({t.name}): id must be an integer in 1..{MAX_TILE_ID}", tid if _is_int(tid) else None)
            continue
        if tid == 0:
            result.add(f"tile 0 ({t.name}): id 0 is reserved for 'empty'", 0)
        if tid in seen_ids:
            result.add(f"tile {tid} ({t.name}): duplicate id (also used by {seen_ids[tid]!r})", tid)
        seen_ids[tid] = t.name

        if not t.name:
            result.add(f"tile {tid}: name is missing", tid)
        elif t.name in seen_names:
            result.add(f"tile {tid} ({t.name}): duplicate name (also id {seen_names[t.name]})", tid)
        seen_names.setdefault(t.name, tid)

        if t.cls not in VALID_CLASSES:
            result.add(f"tile {tid} ({t.name}): class {t.cls!r} not in {list(VALID_CLASSES)}", tid)

        ints_ok = True
        for fname, v in (("col", t.col), ("row", t.row), ("frames", t.frames), ("fps", t.fps)):
            if not _is_int(v):
                result.add(f"tile {tid} ({t.name}): {fname} must be an integer, got {v!r}", tid)
                ints_ok = False
        if not ints_ok:
            continue

        if t.col < 0 or t.row < 0:
            result.add(f"tile {tid} ({t.name}): col/row must be >= 0", tid)
        if t.frames < 1:
            result.add(f"tile {tid} ({t.name}): frames must be >= 1", tid)
        if t.fps < 0:
            result.add(f"tile {tid} ({t.name}): fps must be >= 0", tid)
        if t.frames > 1 and t.fps <= 0:
            result.add(f"tile {tid} ({t.name}): animated ({t.frames} frames) but fps is {t.fps}", tid)
        if t.frames == 1 and t.fps > 0:
            result.add(f"tile {tid} ({t.name}): static (1 frame) but fps is {t.fps}", tid)

        if t.cls in ("terrain", "overlay"):
            if not t.cover:
                result.add(f"tile {tid} ({t.name}): class {t.cls} requires a cover", tid)
            else:
                covers.setdefault(t.cover, set()).add(tid)

        if aw is not None and geometry_ok and t.frames >= 1 and t.col >= 0 and t.row >= 0:
            right = (t.col + t.frames) * ts.cell_width
            bottom = (t.row + 1) * ts.cell_height
            if right > aw or bottom > ah:
                result.add(
                    f"tile {tid} ({t.name}): cells col {t.col}..{t.col + t.frames - 1} row {t.row} "
                    f"need {right}x{bottom} px but atlas is {aw}x{ah}",
                    tid,
                )

        if t.frames >= 1 and t.col >= 0 and t.row >= 0:
            for cell in t.cells():
                if cell in occupied:
                    result.add(f"tile {tid} ({t.name}): cell {cell} already used by tile {occupied[cell]}", tid)
                else:
                    occupied[cell] = tid

    # --- autotile --------------------------------------------------------------
    seen_at: set[str] = set()
    for at in ts.autotiles:
        label = f"autotile {at.cover!r}"
        if not at.cover:
            result.add("autotile entry without cover")
            continue
        if at.cover in seen_at:
            result.add(f"{label}: declared more than once")
        seen_at.add(at.cover)
        if at.cover not in covers:
            result.add(f"{label}: no terrain/overlay tile declares this cover")
        targets = [("default", at.default)] + [(f"masks[{k!r}]", v) for k, v in at.masks.items()]
        for k in at.masks:
            try:
                m = int(k)
            except (TypeError, ValueError):
                result.add(f"{label}: mask key {k!r} is not an integer")
                continue
            if not 0 <= m <= 15:
                result.add(f"{label}: mask key {k!r} out of range 0..15")
        for what, target in targets:
            if not _is_int(target) or target not in seen_ids:
                result.add(f"{label}: {what} references unknown tile id {target!r}")
            elif at.cover in covers and target not in covers[at.cover]:
                result.add(f"{label}: {what} -> tile {target} ({seen_ids[target]}) has a different cover")

    return result


def format_table(result: CheckResult) -> str:
    lines: list[str] = []
    ts = result.tileset
    if ts is not None:
        size = f"{result.atlas_size[0]}x{result.atlas_size[1]}" if result.atlas_size else "missing"
        lines.append(
            f"tileset {ts.id} v{ts.version}  spec v{ts.spec_version}  atlas {ts.atlas} ({size})  "
            f"tile {ts.tile_width}x{ts.tile_height}  cell {ts.cell_width}x{ts.cell_height}  license {ts.license}"
        )
        header = f"{'id':>5} {'name':<18} {'class':<8} {'col':>3} {'row':>3} {'frm':>3} {'fps':>3} {'cover':<18} status"
        lines.append(header)
        lines.append("-" * len(header))
        for t in ts.tiles:
            probs = result.tile_problems.get(t.id, [])
            status = "ok" if not probs else f"FAIL ({len(probs)})"
            lines.append(
                f"{str(t.id):>5} {t.name:<18} {t.cls:<8} {str(t.col):>3} {str(t.row):>3} "
                f"{str(t.frames):>3} {str(t.fps):>3} {(t.cover or '-'):<18} {status}"
            )
        lines.append("")
        lines.append(f"autotile covers: {', '.join(a.cover for a in ts.autotiles) or '(none)'}")
    if result.problems:
        lines.append("")
        lines.append(f"{len(result.problems)} problem(s):")
        lines.extend(f"  - {p}" for p in result.problems)
    else:
        lines.append("")
        lines.append("OK: tiles.toml and atlas.png are consistent")
    return "\n".join(lines)
