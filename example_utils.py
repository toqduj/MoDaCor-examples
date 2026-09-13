"""Small path helpers shared by the example notebooks."""

from __future__ import annotations

from pathlib import Path


def locate_example_dir(relative_path: str | Path, *, start: str | Path | None = None) -> Path:
    """Find an instrument directory from the repository root or the example itself."""

    relative_path = Path(relative_path)
    start_path = Path.cwd() if start is None else Path(start)
    for parent in (start_path.resolve(), *start_path.resolve().parents):
        for candidate in (parent, parent / relative_path):
            if (candidate / "data-manifest.json").is_file() and (candidate / "pipelines").is_dir():
                return candidate
    raise FileNotFoundError(
        f"Could not locate {relative_path.as_posix()}. Start Jupyter from the examples "
        "repository root or the instrument directory."
    )
