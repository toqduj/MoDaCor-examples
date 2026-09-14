"""MOUSE-specific discovery and source construction for the example notebook."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import h5py

BACKGROUND_REFERENCE_PATH = "/entry1/processing_required_metadata/background_file"
DISPERSANT_REFERENCE_PATH = "/entry1/processing_required_metadata/dispersed_background_file"
DISPLACED_DISPERSANT_FACTOR_PATH = "/entry1/sample/matrixfraction"
EMBEDDED_MASK_PATH = "/entry1/instrument/mask/Mask"


def _text(value: Any) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)


def _modacor_ready_path(value: Any, *, relative_to: Path) -> Path:
    path = Path(_text(value))
    if not path.is_absolute():
        path = relative_to / path
    if not path.stem.endswith("_modacor"):
        path = path.with_name(f"{path.stem}_modacor{path.suffix}")
    return path.resolve()


def discover_measurement_pairs(
    data_root: str | Path,
    *,
    sample_glob: str = "MOUSE_*_stacked_modacor.nxs",
    batch_start: int,
    batch_end: int,
) -> list[dict[str, Any]]:
    pairs = []
    for sample in sorted(Path(data_root).glob(sample_glob)):
        with h5py.File(sample, "r") as h5:
            batch = int(h5["/entry1/experiment/batchnum"][()])
            if not batch_start <= batch <= batch_end:
                continue
            configuration = int(h5["/entry1/instrument/configuration"][()])
            background = _modacor_ready_path(h5[BACKGROUND_REFERENCE_PATH][()], relative_to=sample.parent)
            dispersant_value = _text(h5[DISPERSANT_REFERENCE_PATH][()]).strip()
            dispersant = _modacor_ready_path(dispersant_value, relative_to=sample.parent) if dispersant_value else None
            factor = float(h5[DISPLACED_DISPERSANT_FACTOR_PATH][()].mean())
            if EMBEDDED_MASK_PATH not in h5:
                raise KeyError(f"Embedded mask missing from {sample.name}: {EMBEDDED_MASK_PATH}")
        if not background.is_file():
            raise FileNotFoundError(f"Background referenced by {sample.name} is missing: {background}")
        with h5py.File(background, "r") as background_h5:
            background_configuration = int(background_h5["/entry1/instrument/configuration"][()])
        if background_configuration != configuration:
            raise ValueError(
                f"Configuration mismatch for {sample.name}: sample {configuration}, background {background_configuration}."
            )
        use_dispersant = dispersant is not None and 0.0 < factor < 1.0
        if use_dispersant and not dispersant.is_file():
            raise FileNotFoundError(f"Dispersant referenced by {sample.name} is missing: {dispersant}")
        pairs.append(
            {
                "sample": sample.resolve(),
                "background": background,
                "dispersant": dispersant,
                "displaced_dispersant_factor": factor,
                "use_dispersant_pipeline": use_dispersant,
                "batch": batch,
                "configuration": configuration,
            }
        )
    if not pairs:
        raise FileNotFoundError(f"No {sample_glob} files found below {data_root} for batches {batch_start}–{batch_end}.")
    return pairs


def source_registrations(pair: dict[str, Any]) -> list[dict[str, Any]]:
    sources = [
        {"ref": "sample", "type": "hdf", "location": str(pair["sample"])},
        {"ref": "background", "type": "hdf", "location": str(pair["background"])},
    ]
    if pair["use_dispersant_pipeline"]:
        sources.append({"ref": "dispersant", "type": "hdf", "location": str(pair["dispersant"])})
    return sources
