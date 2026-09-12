#!/usr/bin/env python3
"""Validate the portable structure of the MoDaCor examples repository."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from data_repository import (
    DataError,
    read_manifest,
    verify_files,
    verify_license_binding,
)


ROOT = Path(__file__).resolve().parents[1]
PORTABILITY_PATTERNS = {
    "macOS home path": re.compile(r"/Users/[^/\s\"']+"),
    "Linux home path": re.compile(r"/home/[^/\s\"']+"),
    "Windows user path": re.compile(r"[A-Za-z]:\\\\Users\\\\"),
}


class Report:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.pipeline_count = 0
        self.notebook_count = 0
        self.manifest_count = 0
        self.hdf_count = 0
        self.external_link_count = 0

    def error(self, message: str) -> None:
        self.errors.append(message)

    def warning(self, message: str) -> None:
        self.warnings.append(message)


def relative(path: Path) -> str:
    return str(path.relative_to(ROOT))


def validate_foundation(report: Report) -> None:
    for name in (
        "README.md",
        "CONTRIBUTING.md",
        "DATA_POLICY.md",
        "LICENSE",
        "LICENSING.md",
        "RELEASE_PLAN.md",
        ".gitignore",
        ".pre-commit-config.yaml",
    ):
        if not (ROOT / name).is_file():
            report.error(f"missing root file: {name}")

    for path in ROOT.rglob(".DS_Store"):
        report.error(f"macOS metadata must not be archived: {relative(path)}")


def validate_notebooks(report: Report) -> None:
    for path in sorted(ROOT.glob("*/*/*.ipynb")):
        report.notebook_count += 1
        try:
            notebook = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            report.error(f"cannot parse {relative(path)}: {exc}")
            continue

        searchable_parts: list[str] = []
        output_count = 0
        executed_count = 0
        transient_metadata_count = 0
        for cell in notebook.get("cells", []):
            searchable_parts.extend(cell.get("source", []))
            if cell.get("cell_type") == "code":
                output_count += len(cell.get("outputs", []))
                executed_count += cell.get("execution_count") is not None
                transient_metadata_count += sum(
                    key in cell.get("metadata", {}) for key in ("execution", "ExecuteTime")
                )
            for output in cell.get("outputs", []):
                searchable_parts.extend(output.get("text", []))
                searchable_parts.extend(output.get("data", {}).get("text/plain", []))

        searchable = "".join(searchable_parts)
        for label, pattern in PORTABILITY_PATTERNS.items():
            if pattern.search(searchable):
                report.error(f"{relative(path)} contains a {label}")
        if output_count:
            report.error(f"{relative(path)} contains {output_count} saved output(s)")
        if executed_count:
            report.error(
                f"{relative(path)} contains {executed_count} execution count(s)"
            )
        if transient_metadata_count or "widgets" in notebook.get("metadata", {}):
            report.error(f"{relative(path)} contains transient execution metadata")


def validate_data_manifests(
    report: Report, *, require_data: bool, verify_data: bool, release: bool
) -> None:
    notebook_directories = {path.parent for path in ROOT.glob("*/*/*.ipynb")}
    for directory in sorted(notebook_directories):
        if not (directory / "data-manifest.json").is_file():
            report.error(f"implemented example lacks data-manifest.json: {relative(directory)}")

    for path in sorted(ROOT.glob("*/*/data-manifest.json")):
        report.manifest_count += 1
        try:
            manifest = read_manifest(path)
        except DataError as exc:
            report.error(str(exc))
            continue

        try:
            data_license = verify_license_binding(manifest, path.parent)
        except DataError as exc:
            report.error(str(exc))
            data_license = None
        if data_license is None:
            message = f"data license is unresolved for {manifest['instrument']}"
            if release:
                report.error(message)
            else:
                report.warning(message)

        if release:
            if manifest.get("data_version") == "development":
                report.error(f"data version is not frozen for {manifest['instrument']}")
            for field in ("record_id", "doi"):
                if not manifest.get("zenodo", {}).get(field):
                    report.error(
                        f"Zenodo {field} is unset for {manifest['instrument']}"
                    )
            for field in ("url", "size_bytes", "sha256"):
                if not manifest.get("archive", {}).get(field):
                    report.error(
                        f"archive {field} is unset for {manifest['instrument']}"
                    )

        data_dir = path.parent / "data"
        local_files = list(data_dir.rglob("*")) if data_dir.is_dir() else []
        has_local_data = any(candidate.is_file() for candidate in local_files)
        if not has_local_data:
            message = f"data not downloaded for {manifest['instrument']}"
            if require_data:
                report.error(message)
            else:
                report.warning(message)
            continue

        try:
            verify_files(manifest, path.parent, calculate_hashes=verify_data)
        except DataError as exc:
            report.error(str(exc))


def validate_pipelines(report: Report) -> None:
    try:
        from modacor.runner.pipeline import Pipeline
    except ImportError:
        report.warning("MoDaCor is unavailable; pipeline preparation was skipped")
        return

    for path in sorted(ROOT.glob("*/*/pipelines/*.yaml")):
        report.pipeline_count += 1
        try:
            pipeline = Pipeline.from_yaml_file(yaml_file=path)
            pipeline.prepare()
        except Exception as exc:  # pipeline exceptions are intentionally surfaced
            report.error(f"cannot prepare {relative(path)}: {type(exc).__name__}: {exc}")


def validate_hdf5_links(report: Report) -> None:
    try:
        import h5py
    except ImportError:
        report.warning("h5py is unavailable; HDF5 external-link checks were skipped")
        return

    def walk_links(file_path: Path, group, prefix: str = "") -> None:
        for name in group:
            object_path = f"{prefix}/{name}"
            link = group.get(name, getlink=True)
            if isinstance(link, h5py.ExternalLink):
                report.external_link_count += 1
                target = Path(link.filename)
                if not target.is_absolute():
                    target = file_path.parent / target
                if not target.exists():
                    report.error(
                        f"broken HDF5 external link in {relative(file_path)}: "
                        f"{object_path} -> {link.filename}"
                    )
                continue
            try:
                child = group.get(name)
            except Exception as exc:
                report.error(
                    f"cannot resolve HDF5 object {relative(file_path)}:{object_path}: {exc}"
                )
                continue
            if isinstance(child, h5py.Group):
                walk_links(file_path, child, object_path)

    for path in sorted(ROOT.glob("*/*/data/**/*.nxs")):
        report.hdf_count += 1
        try:
            with h5py.File(path, "r") as handle:
                walk_links(path, handle)
        except OSError as exc:
            report.error(f"cannot open {relative(path)}: {exc}")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument(
        "--require-data",
        action="store_true",
        help="fail when an implemented instrument's data have not been downloaded",
    )
    result.add_argument(
        "--verify-data",
        action="store_true",
        help="calculate and compare every local data-file SHA-256 checksum",
    )
    result.add_argument(
        "--release",
        action="store_true",
        help="enforce data, license, frozen-version, archive, and Zenodo release gates",
    )
    return result


def main() -> int:
    args = parser().parse_args()
    if args.release:
        args.require_data = True
        args.verify_data = True
    report = Report()
    validate_foundation(report)
    validate_notebooks(report)
    validate_data_manifests(
        report,
        require_data=args.require_data,
        verify_data=args.verify_data,
        release=args.release,
    )
    validate_pipelines(report)
    validate_hdf5_links(report)

    print(
        "Checked "
        f"{report.notebook_count} notebook(s), "
        f"{report.pipeline_count} pipeline(s), "
        f"{report.manifest_count} data manifest(s), "
        f"{report.hdf_count} NeXus file(s), and "
        f"{report.external_link_count} external link(s)."
    )
    for warning in report.warnings:
        print(f"WARNING: {warning}")
    for error in report.errors:
        print(f"ERROR: {error}")
    if report.errors:
        print(f"Validation failed with {len(report.errors)} error(s).")
        return 1
    print("Validation passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
