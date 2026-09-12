#!/usr/bin/env python3
"""Manage ignored instrument data backed by versioned Zenodo archives."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_NAME = "data-manifest.json"
DATA_LICENSE_DECLARATION = "DATA_LICENSE.json"
SCHEMA_VERSION = 1
HASH_CHUNK_SIZE = 8 * 1024 * 1024


class DataError(RuntimeError):
    """Raised for an invalid manifest, archive, or local data tree."""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(HASH_CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def instrument_dir(identifier: str) -> Path:
    path = (ROOT / identifier).resolve()
    try:
        relative = path.relative_to(ROOT)
    except ValueError as exc:
        raise DataError(f"instrument is outside the repository: {identifier}") from exc
    if len(relative.parts) != 2:
        raise DataError("instrument identifiers must have the form FACILITY/INSTRUMENT")
    return path


def instrument_identifier(path: Path) -> str:
    return path.resolve().relative_to(ROOT).as_posix()


def manifest_path(identifier: str) -> Path:
    return instrument_dir(identifier) / MANIFEST_NAME


def read_manifest(path: Path) -> dict:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise DataError(f"missing manifest: {path.relative_to(ROOT)}") from exc
    except json.JSONDecodeError as exc:
        raise DataError(f"invalid JSON in {path.relative_to(ROOT)}: {exc}") from exc
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise DataError(f"unsupported schema in {path.relative_to(ROOT)}")
    if manifest.get("instrument") != instrument_identifier(path.parent):
        raise DataError(f"instrument mismatch in {path.relative_to(ROOT)}")
    if not isinstance(manifest.get("files"), list) or not manifest["files"]:
        raise DataError(f"manifest has no files: {path.relative_to(ROOT)}")
    return manifest


def write_manifest(path: Path, manifest: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def discover_data_instruments() -> list[str]:
    return [
        instrument_identifier(path.parent)
        for path in sorted(ROOT.glob("*/*/data"))
        if path.is_dir()
    ]


def discover_manifest_instruments() -> list[str]:
    return [
        instrument_identifier(path.parent)
        for path in sorted(ROOT.glob(f"*/*/{MANIFEST_NAME}"))
    ]


def select_instruments(requested: list[str], *, from_data: bool = False) -> list[str]:
    available = discover_data_instruments() if from_data else discover_manifest_instruments()
    selected = available if not requested else requested
    if not selected:
        kind = "data directories" if from_data else "data manifests"
        raise DataError(f"no {kind} found")
    unknown = sorted(set(selected) - set(available))
    if unknown:
        raise DataError(f"unknown or unavailable instrument(s): {', '.join(unknown)}")
    return sorted(dict.fromkeys(selected))


def file_entries(data_dir: Path) -> list[dict]:
    files = [path for path in sorted(data_dir.rglob("*")) if path.is_file()]
    if not files:
        raise DataError(f"no files found below {data_dir.relative_to(ROOT)}")
    entries = []
    for path in files:
        if path.name == ".DS_Store":
            continue
        entries.append(
            {
                "path": path.relative_to(data_dir.parent).as_posix(),
                "size_bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
        )
    return entries


def new_archive_metadata(identifier: str) -> dict:
    return {
        "filename": f"{identifier.replace('/', '-')}-data.zip",
        "url": None,
        "size_bytes": None,
        "sha256": None,
    }


def resolve_data_license(directory: Path) -> dict | None:
    candidates = (
        directory / DATA_LICENSE_DECLARATION,
        directory.parent / DATA_LICENSE_DECLARATION,
    )
    for declaration_path in candidates:
        if not declaration_path.is_file():
            continue
        try:
            declaration = json.loads(declaration_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise DataError(
                f"invalid JSON in {declaration_path.relative_to(ROOT)}: {exc}"
            ) from exc
        if declaration.get("schema_version") != SCHEMA_VERSION:
            raise DataError(
                f"unsupported data-license schema: {declaration_path.relative_to(ROOT)}"
            )
        for field in ("spdx_id", "name", "license_file"):
            if not isinstance(declaration.get(field), str) or not declaration[field].strip():
                raise DataError(
                    f"missing data-license {field}: {declaration_path.relative_to(ROOT)}"
                )
        for field in ("license_url", "attribution"):
            if field in declaration and (
                not isinstance(declaration[field], str) or not declaration[field].strip()
            ):
                raise DataError(
                    f"invalid data-license {field}: {declaration_path.relative_to(ROOT)}"
                )

        license_relative = Path(declaration["license_file"])
        if license_relative.is_absolute() or ".." in license_relative.parts:
            raise DataError(
                f"unsafe license_file in {declaration_path.relative_to(ROOT)}"
            )
        license_path = (declaration_path.parent / license_relative).resolve()
        if not license_path.is_file():
            raise DataError(f"missing data-license text: {license_path.relative_to(ROOT)}")

        resolved = {
            "spdx_id": declaration["spdx_id"],
            "name": declaration["name"],
            "declaration": Path(
                os.path.relpath(declaration_path, start=directory)
            ).as_posix(),
            "license_file": Path(os.path.relpath(license_path, start=directory)).as_posix(),
            "license_sha256": sha256(license_path),
        }
        for field in ("license_url", "attribution"):
            if field in declaration:
                resolved[field] = declaration[field]
        return resolved
    return None


def verify_license_binding(
    manifest: dict, directory: Path, *, required: bool = False
) -> dict | None:
    resolved = resolve_data_license(directory)
    recorded = manifest.get("data_license")
    if recorded != resolved:
        raise DataError(
            f"data-license binding changed for {instrument_identifier(directory)}; "
            "run update-manifests"
        )
    if required and resolved is None:
        raise DataError(
            f"no facility- or instrument-level data license for "
            f"{instrument_identifier(directory)}"
        )
    return resolved


def update_manifests(requested: list[str]) -> None:
    for identifier in select_instruments(requested, from_data=True):
        directory = instrument_dir(identifier)
        path = directory / MANIFEST_NAME
        old = read_manifest(path) if path.exists() else None
        entries = file_entries(directory / "data")
        data_license = resolve_data_license(directory)
        content_changed = (
            old is None
            or old.get("files") != entries
            or old.get("data_license") != data_license
        )

        if content_changed:
            archive = new_archive_metadata(identifier)
            zenodo = {
                "record_id": None,
                "doi": None,
                "concept_doi": (old or {}).get("zenodo", {}).get("concept_doi"),
            }
            data_version = "development"
        else:
            archive = old["archive"]
            zenodo = old["zenodo"]
            data_version = old["data_version"]

        manifest = {
            "schema_version": SCHEMA_VERSION,
            "instrument": identifier,
            "data_version": data_version,
            "data_license": data_license,
            "zenodo": zenodo,
            "archive": archive,
            "files": entries,
        }
        write_manifest(path, manifest)
        state = "changed; release binding cleared" if content_changed else "unchanged"
        total = sum(entry["size_bytes"] for entry in entries)
        print(f"{identifier}: {len(entries)} files, {total} bytes ({state})")


def verify_files(manifest: dict, base: Path, *, calculate_hashes: bool = True) -> None:
    expected_paths = set()
    for entry in manifest["files"]:
        relative = Path(entry["path"])
        if relative.is_absolute() or ".." in relative.parts or relative.parts[:1] != ("data",):
            raise DataError(f"unsafe manifest path: {entry['path']}")
        expected_paths.add(relative.as_posix())
        path = base / relative
        if not path.is_file():
            raise DataError(f"missing data file: {path}")
        actual_size = path.stat().st_size
        if actual_size != entry["size_bytes"]:
            raise DataError(
                f"size mismatch for {path}: {actual_size} != {entry['size_bytes']}"
            )
        if calculate_hashes:
            actual_hash = sha256(path)
            if actual_hash != entry["sha256"]:
                raise DataError(f"SHA-256 mismatch for {path}")

    actual_paths = {
        path.relative_to(base).as_posix()
        for path in (base / "data").rglob("*")
        if path.is_file() and path.name != ".DS_Store"
    }
    unexpected = sorted(actual_paths - expected_paths)
    if unexpected:
        raise DataError(f"unexpected data file(s): {', '.join(unexpected)}")


def verify(requested: list[str]) -> None:
    for identifier in select_instruments(requested):
        manifest = read_manifest(manifest_path(identifier))
        directory = instrument_dir(identifier)
        verify_license_binding(manifest, directory)
        verify_files(manifest, directory)
        print(f"{identifier}: verified {len(manifest['files'])} files")


def record_archive(path: Path, manifest: dict, archive_path: Path) -> None:
    manifest["archive"]["size_bytes"] = archive_path.stat().st_size
    manifest["archive"]["sha256"] = sha256(archive_path)
    manifest["archive"]["url"] = None
    manifest["zenodo"]["record_id"] = None
    manifest["zenodo"]["doi"] = None
    manifest["data_version"] = "development"
    write_manifest(path, manifest)


def package(requested: list[str], output_dir: Path, *, record: bool) -> None:
    for identifier in select_instruments(requested):
        path = manifest_path(identifier)
        manifest = read_manifest(path)
        directory = instrument_dir(identifier)
        data_license = verify_license_binding(manifest, directory, required=True)
        verify_files(manifest, directory)
        output_dir.mkdir(parents=True, exist_ok=True)
        archive_path = output_dir / manifest["archive"]["filename"]
        temporary = archive_path.with_suffix(archive_path.suffix + ".tmp")
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_STORED) as archive:
            for entry in manifest["files"]:
                archive.write(directory / entry["path"], arcname=entry["path"])
            archive.write(
                directory / data_license["license_file"],
                arcname="DATA_LICENSE.txt",
            )
        temporary.replace(archive_path)
        archive_hash = sha256(archive_path)
        try:
            displayed_path = archive_path.relative_to(ROOT)
        except ValueError:
            displayed_path = archive_path
        print(
            f"{identifier}: wrote {displayed_path} "
            f"({archive_path.stat().st_size} bytes, sha256={archive_hash})"
        )
        if record:
            record_archive(path, manifest, archive_path)


def validate_archive_members(archive: zipfile.ZipFile, manifest: dict) -> None:
    expected = {entry["path"] for entry in manifest["files"]}
    expected.add("DATA_LICENSE.txt")
    actual = set()
    for member in archive.infolist():
        path = Path(member.filename)
        if path.is_absolute() or ".." in path.parts or "\\" in member.filename:
            raise DataError(f"unsafe ZIP member: {member.filename}")
        file_type = (member.external_attr >> 16) & 0o170000
        if file_type == stat.S_IFLNK:
            raise DataError(f"symbolic links are not allowed in data archives: {member.filename}")
        if not member.is_dir():
            actual.add(path.as_posix())
    if actual != expected:
        missing = sorted(expected - actual)
        unexpected = sorted(actual - expected)
        raise DataError(f"archive content mismatch; missing={missing}, unexpected={unexpected}")


def download_file(url: str, destination: Path) -> None:
    temporary = destination.with_suffix(destination.suffix + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": "MoDaCor-examples/1"})
    with urllib.request.urlopen(request) as response, temporary.open("wb") as output:
        shutil.copyfileobj(response, output, length=HASH_CHUNK_SIZE)
    temporary.replace(destination)


def download_one(identifier: str, *, force: bool) -> None:
    path = manifest_path(identifier)
    manifest = read_manifest(path)
    directory = instrument_dir(identifier)
    data_license = verify_license_binding(manifest, directory, required=True)
    data_dir = directory / "data"

    if data_dir.exists():
        try:
            verify_files(manifest, directory)
            print(f"{identifier}: local data already match the manifest")
            return
        except DataError:
            if not force:
                raise DataError(
                    f"{identifier}: local data are incomplete or changed; rerun with --force "
                    "to replace them while retaining a backup"
                )

    archive_metadata = manifest["archive"]
    for field in ("url", "size_bytes", "sha256"):
        if not archive_metadata.get(field):
            raise DataError(f"{identifier}: archive {field} is not set in the manifest")

    downloads = directory / "work" / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    archive_path = downloads / archive_metadata["filename"]
    if not archive_path.exists():
        print(f"{identifier}: downloading {archive_metadata['url']}")
        download_file(archive_metadata["url"], archive_path)

    if archive_path.stat().st_size != archive_metadata["size_bytes"]:
        raise DataError(f"{identifier}: downloaded archive size does not match the manifest")
    if sha256(archive_path) != archive_metadata["sha256"]:
        raise DataError(f"{identifier}: downloaded archive SHA-256 does not match the manifest")

    with tempfile.TemporaryDirectory(prefix="extract-", dir=directory / "work") as temporary:
        extraction_root = Path(temporary)
        with zipfile.ZipFile(archive_path) as archive:
            validate_archive_members(archive, manifest)
            archive.extractall(extraction_root)
        verify_files(manifest, extraction_root)
        extracted_license = extraction_root / "DATA_LICENSE.txt"
        if sha256(extracted_license) != data_license["license_sha256"]:
            raise DataError(f"{identifier}: archived data-license text does not match")

        backup = None
        if data_dir.exists():
            backup = directory / "work" / "replaced-data-backup"
            if backup.exists():
                raise DataError(f"backup already exists; move or remove it first: {backup}")
            data_dir.replace(backup)
        try:
            (extraction_root / "data").replace(data_dir)
        except Exception:
            if backup is not None and not data_dir.exists():
                backup.replace(data_dir)
            raise

    suffix = f"; previous data retained at {backup}" if backup else ""
    print(f"{identifier}: installed {len(manifest['files'])} verified files{suffix}")


def download(requested: list[str], *, all_instruments: bool, force: bool) -> None:
    if all_instruments:
        if requested:
            raise DataError("use explicit instruments or --all, not both")
        selected = select_instruments([])
    else:
        if not requested:
            raise DataError("specify at least one FACILITY/INSTRUMENT or use --all")
        selected = select_instruments(requested)
    for identifier in selected:
        download_one(identifier, force=force)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)

    update = commands.add_parser("update-manifests", help="hash local data trees")
    update.add_argument("instruments", nargs="*", metavar="FACILITY/INSTRUMENT")

    verify_command = commands.add_parser("verify", help="verify local data against manifests")
    verify_command.add_argument("instruments", nargs="*", metavar="FACILITY/INSTRUMENT")

    package_command = commands.add_parser("package", help="build per-instrument ZIP archives")
    package_command.add_argument("instruments", nargs="*", metavar="FACILITY/INSTRUMENT")
    package_command.add_argument("--all", action="store_true", help="package all manifests")
    package_command.add_argument(
        "--output-dir", type=Path, default=ROOT / "dist" / "data"
    )
    package_command.add_argument(
        "--record-archive",
        action="store_true",
        help="record archive size/checksum and clear its unpublished Zenodo binding",
    )

    download_command = commands.add_parser("download", help="download and install data")
    download_command.add_argument("instruments", nargs="*", metavar="FACILITY/INSTRUMENT")
    download_command.add_argument("--all", action="store_true", help="download all manifests")
    download_command.add_argument(
        "--force",
        action="store_true",
        help="replace invalid local data while retaining it below work/",
    )
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        if args.command == "update-manifests":
            update_manifests(args.instruments)
        elif args.command == "verify":
            verify(args.instruments)
        elif args.command == "package":
            if args.all and args.instruments:
                raise DataError("use explicit instruments or --all, not both")
            requested = [] if args.all else args.instruments
            if not requested and not args.all:
                raise DataError("specify at least one FACILITY/INSTRUMENT or use --all")
            package(requested, args.output_dir.resolve(), record=args.record_archive)
        elif args.command == "download":
            download(args.instruments, all_instruments=args.all, force=args.force)
    except (DataError, OSError, zipfile.BadZipFile) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
