"""`check` passes on the repo and fails on broken fixtures (with a non-zero CLI exit)."""

import shutil
from pathlib import Path

from bramapa_tileset.check import check, format_table
from bramapa_tileset.cli import main


def test_check_passes_on_repo(repo_root: Path) -> None:
    result = check(repo_root / "tiles.toml")
    assert result.ok, "\n".join(result.problems)
    assert result.atlas_size == (1024, 1024)
    assert "OK" in format_table(result)


def test_check_cli_exit_codes(repo_root: Path, fixtures_dir: Path, capsys) -> None:
    assert main(["check", "--tiles", str(repo_root / "tiles.toml")]) == 0
    out = capsys.readouterr().out
    assert "bramapa-base" in out and "arvore" in out

    rc = main(["check", "--tiles", str(fixtures_dir / "tiles_out_of_bounds.toml"), "--atlas", str(repo_root / "atlas.png")])
    assert rc == 1
    out = capsys.readouterr().out
    assert "FAIL" in out and "problem(s)" in out


def test_check_fails_on_out_of_bounds_tile(repo_root: Path, fixtures_dir: Path) -> None:
    result = check(fixtures_dir / "tiles_out_of_bounds.toml", repo_root / "atlas.png")
    assert not result.ok
    assert any("tile 7 (montanha)" in p and "atlas is 1024x1024" in p for p in result.problems), result.problems
    assert 7 in result.tile_problems


def test_check_reports_semantic_problems(repo_root: Path, fixtures_dir: Path) -> None:
    result = check(fixtures_dir / "tiles_semantic_errors.toml", repo_root / "atlas.png")
    joined = "\n".join(result.problems)
    assert "duplicate id" in joined
    assert "id 0 is reserved" in joined
    assert "class 'decal'" in joined
    assert "animated (4 frames) but fps is 0" in joined
    assert "autotile 'lava'" in joined and "no terrain/overlay tile declares this cover" in joined
    assert "already used by tile" in joined


def test_check_fails_when_atlas_missing(repo_root: Path, tmp_path: Path) -> None:
    shutil.copy(repo_root / "tiles.toml", tmp_path / "tiles.toml")
    result = check(tmp_path / "tiles.toml")
    assert not result.ok
    assert any("atlas not found" in p for p in result.problems)


def test_check_warns_on_incomplete_autotile_masks(repo_root: Path) -> None:
    result = check(repo_root / "tiles.toml")
    assert result.ok
    # agua/oceano list all 16 masks; rio relies on its default and gets a WARN, not a failure.
    assert len(result.warnings) == 1
    assert result.warnings[0].startswith("autotile 'rio': masks 1, 2, 3, 4, 6")
    assert "WARN autotile 'rio'" in format_table(result)


def test_check_fails_without_default_and_incomplete_masks(repo_root: Path, tmp_path: Path) -> None:
    src = (repo_root / "tiles.toml").read_text()
    broken = src.replace('cover = "rio"\ndefault = 21\n', 'cover = "rio"\n')
    assert broken != src
    (tmp_path / "tiles.toml").write_text(broken)
    result = check(tmp_path / "tiles.toml", repo_root / "atlas.png")
    assert any("autotile 'rio': no default and masks 1, 2, 3" in p for p in result.problems), result.problems
