#!/usr/bin/env python3
"""Remove execution state from notebooks before they enter Git history."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TRANSIENT_CELL_METADATA = ("execution", "ExecuteTime")


def strip_notebook(path: Path) -> bool:
    notebook = json.loads(path.read_text(encoding="utf-8"))
    changed = False

    metadata = notebook.setdefault("metadata", {})
    if "widgets" in metadata:
        del metadata["widgets"]
        changed = True

    for cell in notebook.get("cells", []):
        if cell.get("cell_type") != "code":
            continue
        if cell.get("outputs"):
            cell["outputs"] = []
            changed = True
        if cell.get("execution_count") is not None:
            cell["execution_count"] = None
            changed = True
        cell_metadata = cell.setdefault("metadata", {})
        for key in TRANSIENT_CELL_METADATA:
            if key in cell_metadata:
                del cell_metadata[key]
                changed = True

    if changed:
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(notebook, indent=1, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
    return changed


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("notebooks", nargs="*", type=Path)
    return result


def main() -> int:
    args = parser().parse_args()
    paths = args.notebooks or sorted(ROOT.glob("*/*/*.ipynb"))
    try:
        for path in paths:
            if strip_notebook(path):
                print(f"Stripped notebook execution state: {path}")
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
