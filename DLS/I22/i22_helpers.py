"""I22-specific data preparation and chunk-plan helpers for the notebooks."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import h5py
import numpy as np
import yaml

from modacor.client import SourceBufferClient
from modacor.io.chunking import (
    AxisSelector,
    ChunkArrayLayout,
    ChunkOutputLayout,
    ChunkPlacement,
    ChunkPlan,
    ChunkSourceBinding,
    ChunkSpec,
    PlacementBinding,
)

DETECTOR_DATASETS = {
    "SAXS": "/entry1/detector/data",
    "WAXS": "/entry1/Pilatus2M_WAXS/data",
}
WAXS_PIPELINES = {
    "usaxs_saxs_waxs": "I22_WAXS_solids_operando_usaxs_saxs_waxs.yaml",
    "standard_saxs_waxs": "I22_WAXS_solids_operando_standard_saxs_waxs.yaml",
}
PREPROCESSING_VERSION = "2026-09-14-i22-transmission-v5"
BSDIODES_CHANNEL = 1
I0_CHANNEL = 1


@dataclass(frozen=True, slots=True)
class I22Inputs:
    project_dir: Path
    beamline_configuration: str
    pipeline_paths: dict[str, Path]
    calibration_files: dict[str, Path]
    mask_files: dict[str, Path]
    background_file: Path
    transmission_reference_file: Path
    sample_files: tuple[Path, ...]
    preprocessed_samples: tuple[Path, ...]
    preprocessed_background: Path

    @property
    def work_dir(self) -> Path:
        return self.project_dir / "work"

    @property
    def measurements(self) -> tuple[tuple[Path, Path], ...]:
        return tuple(zip(self.sample_files, self.preprocessed_samples, strict=True))


@dataclass(frozen=True, slots=True)
class ChunkWorkItem:
    measurement_index: int
    chunk_index: int
    master_path: Path
    source_path: Path
    start: int
    stop: int


def _decode(value: Any) -> Any:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    if isinstance(value, np.ndarray) and value.shape == ():
        return _decode(value.item())
    return value


def _detector_divisor(array: Any) -> np.ndarray:
    values = np.asarray(array)
    return values.reshape(values.shape + (1, 1))


def _frame_array(values: Any, leading_shape: tuple[int, ...], *, name: str) -> np.ndarray:
    values = np.asarray(values, dtype=float)
    if values.shape == leading_shape:
        return values
    if values.size == 1:
        return np.full(leading_shape, float(values.reshape(-1)[0]), dtype=float)
    raise ValueError(f"{name} shape {values.shape} cannot be broadcast to frame shape {leading_shape}.")


def _write_dataset(group: h5py.Group, name: str, values: Any, *, units: str | None = None, **attrs: Any):
    dataset = group.create_dataset(name, data=values)
    if units is not None:
        dataset.attrs["units"] = units
    for key, value in attrs.items():
        dataset.attrs[key] = value
    return dataset


def _readout_statistics(
    source: h5py.File,
    path: str,
    *,
    channel: int,
    leading_shape: tuple[int, ...] | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    values = np.asarray(source[path][()], dtype=float)
    if values.ndim != 4 or channel >= values.shape[-1]:
        raise ValueError(f"Unexpected readout shape {values.shape} at {path}; channel {channel} is unavailable.")
    if leading_shape is not None and tuple(values.shape[:2]) != leading_shape:
        raise ValueError(f"{path} leading dimensions {values.shape[:2]} do not match detector frames {leading_shape}.")

    samples = values[..., channel]
    valid = np.isfinite(samples)
    valid_count = np.sum(valid, axis=-1).astype(np.int32)
    total = np.sum(np.where(valid, samples, 0.0), axis=-1)
    mean = np.divide(total, valid_count, out=np.full(total.shape, np.nan), where=valid_count > 0)
    deviations = np.where(valid, samples - mean[..., None], 0.0)
    variance = np.divide(
        np.sum(deviations**2, axis=-1),
        valid_count - 1,
        out=np.full(total.shape, np.nan),
        where=valid_count > 1,
    )
    std = np.sqrt(variance)
    sem = np.divide(std, np.sqrt(valid_count), out=np.full(total.shape, np.nan), where=valid_count > 0)
    return mean, std, sem, valid_count


def _reference_readout_statistics(
    source: h5py.File, path: str, *, channel: int
) -> tuple[float, float, int]:
    values = np.asarray(source[path][()], dtype=float)
    if values.ndim != 4 or channel >= values.shape[-1]:
        raise ValueError(f"Unexpected readout shape {values.shape} at {path}; channel {channel} is unavailable.")
    samples = values[..., channel]
    samples = samples[np.isfinite(samples)]
    if samples.size < 2:
        raise ValueError(f"{path} needs at least two finite channel-{channel} values for a reference SEM.")
    mean = float(np.mean(samples))
    sem = float(np.std(samples, ddof=1) / np.sqrt(samples.size))
    return mean, sem, int(samples.size)


def _preprocessing_config(
    *, absolute_intensity_factor: float, transmission_reference_file: str
) -> dict[str, Any]:
    if not np.isfinite(absolute_intensity_factor) or absolute_intensity_factor <= 0:
        raise ValueError("absolute_intensity_factor must be a positive finite number.")
    return {
        "version": PREPROCESSING_VERSION,
        "bsdiodes_channel": BSDIODES_CHANNEL,
        "i0_channel": I0_CHANNEL,
        "transmission_reference_file": transmission_reference_file,
        "absolute_intensity_factor": float(absolute_intensity_factor),
    }


def _preprocessing_signature(config: dict[str, Any]) -> str:
    encoded = json.dumps(config, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _needs_rewrite(output_file: Path, *, overwrite: bool, expected_signature: str) -> bool:
    if overwrite or not output_file.exists():
        return True
    try:
        with h5py.File(output_file, "r") as h5:
            return _decode(h5.attrs.get("preprocessing_signature", "")) != expected_signature
    except OSError:
        return True


def preprocess_measurement(
    master_file: str | Path,
    output_dir: str | Path,
    *,
    transmission_reference_file: str | Path,
    absolute_intensity_factor: float = 3.8e-15,
    overwrite: bool = False,
) -> Path:
    """Reshape frame metadata while leaving detector images in their original files."""

    master_file = Path(master_file).resolve()
    output_file = Path(output_dir) / f"{master_file.stem}_modacor.nxs"
    transmission_reference_file = Path(transmission_reference_file).resolve()
    relative_reference = os.path.relpath(transmission_reference_file, start=output_file.parent)
    config = _preprocessing_config(
        absolute_intensity_factor=absolute_intensity_factor,
        transmission_reference_file=relative_reference,
    )
    signature = _preprocessing_signature(config)
    if not _needs_rewrite(output_file, overwrite=overwrite, expected_signature=signature):
        return output_file

    with h5py.File(transmission_reference_file, "r") as reference:
        reference_diode_mean, reference_diode_sem, reference_diode_count = _reference_readout_statistics(
            reference, "/entry1/bsdiodes/data", channel=BSDIODES_CHANNEL
        )
        reference_i0_mean, reference_i0_sem, reference_i0_count = _reference_readout_statistics(
            reference, "/entry1/I0/data", channel=I0_CHANNEL
        )
    if reference_diode_mean == 0.0 or reference_i0_mean == 0.0:
        raise ValueError("The transmission-reference readout means must be non-zero.")
    reference_ratio = reference_diode_mean / reference_i0_mean
    reference_ratio_sem = abs(reference_ratio) * np.hypot(
        reference_diode_sem / reference_diode_mean,
        reference_i0_sem / reference_i0_mean,
    )

    with h5py.File(master_file, "r") as source:
        detector_shapes = {name: tuple(source[path].shape) for name, path in DETECTOR_DATASETS.items()}
        leading_shape = detector_shapes["SAXS"][:-2]
        if detector_shapes["WAXS"][:-2] != leading_shape:
            raise ValueError(f"Incompatible I22 detector leading dimensions: {detector_shapes}.")
        diode_mean, diode_std, diode_sem, diode_count = _readout_statistics(
            source, "/entry1/bsdiodes/data", channel=BSDIODES_CHANNEL, leading_shape=leading_shape
        )
        i0_mean, i0_std, i0_sem, i0_count = _readout_statistics(
            source, "/entry1/I0/data", channel=I0_CHANNEL, leading_shape=leading_shape
        )
        if np.any(diode_mean == 0.0) or np.any(i0_mean == 0.0):
            raise ValueError("Measurement readout means must be non-zero when calculating transmission.")
        transmission = (diode_mean / i0_mean) / reference_ratio
        transmission_sem = np.abs(transmission) * np.sqrt(
            (diode_sem / diode_mean) ** 2
            + (i0_sem / i0_mean) ** 2
            + (reference_ratio_sem / reference_ratio) ** 2
        )
        count_times = {}
        for detector, path in {
            "SAXS": "/entry1/instrument/detector/count_time",
            "WAXS": "/entry1/instrument/Pilatus2M_WAXS/count_time",
        }.items():
            dataset = source[path]
            count_times[detector] = (
                _frame_array(dataset[()], leading_shape, name=path),
                str(_decode(dataset.attrs.get("units", "s"))),
            )
        entry_attrs = dict(source["/entry1"].attrs)
        entry_children = tuple(source["/entry1"].keys())
        sample_attrs = dict(source["/entry1/sample"].attrs) if "/entry1/sample" in source else {}
        sample_children = tuple(source["/entry1/sample"].keys()) if "/entry1/sample" in source else ()

    output_file.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_file.with_suffix(output_file.suffix + ".tmp")
    with h5py.File(temporary, "w") as target:
        relative_master = os.path.relpath(master_file, start=output_file.parent)
        entry = target.create_group("entry1")
        entry.attrs.update(entry_attrs)
        for child in entry_children:
            if child != "sample":
                entry[child] = h5py.ExternalLink(relative_master, f"/entry1/{child}")
        sample = entry.create_group("sample")
        sample.attrs.update(sample_attrs)
        sample.attrs.setdefault("NX_class", "NXsample")
        for child in sample_children:
            if child not in {"transmission", "transmission_sem"}:
                sample[child] = h5py.ExternalLink(relative_master, f"/entry1/sample/{child}")
        target.attrs.update(
            creator="I22 MoDaCor examples helper",
            source_file=relative_master,
            preprocessing_version=PREPROCESSING_VERSION,
            preprocessing_signature=signature,
            preprocessing_config_json=json.dumps(config, sort_keys=True),
        )
        normalization = target.require_group("/modacor/normalization")
        normalization.attrs.update(
            description="Frame-wise arrays reshaped for broadcasting over detector y/x axes.",
            frame_shape=leading_shape,
            bsdiodes_source="/entry1/bsdiodes/data",
            bsdiodes_reduction_axis=2,
            bsdiodes_channel_index=BSDIODES_CHANNEL,
            i0_source="/entry1/I0/data",
            i0_reduction_axis=2,
            i0_channel_index=I0_CHANNEL,
        )
        calibration = target.require_group("/modacor/calibration")
        calibration.attrs["description"] = "Scalar calibration values used by the I22 MoDaCor pipelines."
        calibration.attrs["transmission_reference_file"] = relative_reference
        _write_dataset(
            calibration,
            "absolute_intensity_factor",
            np.asarray(absolute_intensity_factor, dtype=float),
            units="dimensionless",
            source="DAWN processing factor for this example (provisional)",
        )
        _write_dataset(normalization, "bsdiodes_channel_1_mean", _detector_divisor(diode_mean), units="dimensionless")
        _write_dataset(normalization, "bsdiodes_channel_1_std", _detector_divisor(diode_std), units="dimensionless")
        _write_dataset(normalization, "bsdiodes_channel_1_sem", _detector_divisor(diode_sem), units="dimensionless")
        _write_dataset(normalization, "bsdiodes_channel_1_n_valid", _detector_divisor(diode_count))
        _write_dataset(normalization, "i0_channel_1_mean", _detector_divisor(i0_mean), units="dimensionless")
        _write_dataset(normalization, "i0_channel_1_std", _detector_divisor(i0_std), units="dimensionless")
        _write_dataset(normalization, "i0_channel_1_sem", _detector_divisor(i0_sem), units="dimensionless")
        _write_dataset(normalization, "i0_channel_1_n_valid", _detector_divisor(i0_count))
        _write_dataset(
            calibration,
            "bsdiodes_to_i0_ratio",
            reference_ratio,
            units="dimensionless",
            source=relative_reference,
        )
        _write_dataset(calibration, "bsdiodes_to_i0_ratio_sem", reference_ratio_sem, units="dimensionless")
        _write_dataset(calibration, "reference_bsdiodes_mean", reference_diode_mean, units="dimensionless")
        _write_dataset(calibration, "reference_bsdiodes_sem", reference_diode_sem, units="dimensionless")
        _write_dataset(calibration, "reference_bsdiodes_n_valid", reference_diode_count)
        _write_dataset(calibration, "reference_i0_mean", reference_i0_mean, units="dimensionless")
        _write_dataset(calibration, "reference_i0_sem", reference_i0_sem, units="dimensionless")
        _write_dataset(calibration, "reference_i0_n_valid", reference_i0_count)
        _write_dataset(
            sample,
            "transmission",
            _detector_divisor(transmission),
            units="dimensionless",
            long_name="Sample transmission derived from calibrated bsdiodes/I0 readouts",
        )
        _write_dataset(sample, "transmission_sem", _detector_divisor(transmission_sem), units="dimensionless")
        for detector, (values, units) in count_times.items():
            _write_dataset(normalization, f"{detector.lower()}_count_time", _detector_divisor(values), units=units)
    temporary.replace(output_file)
    return output_file


def prepare_inputs(
    project_dir: str | Path,
    *,
    sample_glob: str = "i22-978???.nxs",
    beamline_configuration: str = "usaxs_saxs_waxs",
    transmission_reference_file: str | Path | None = None,
    absolute_intensity_factor: float = 3.8e-15,
    overwrite: bool = False,
) -> I22Inputs:
    """Discover, validate, and minimally preprocess the packaged I22 inputs."""

    project_dir = Path(project_dir).resolve()
    if beamline_configuration not in WAXS_PIPELINES:
        choices = ", ".join(sorted(WAXS_PIPELINES))
        raise ValueError(f"Unknown I22 beamline configuration {beamline_configuration!r}; choose from {choices}.")
    data_dir = project_dir / "data"
    background = data_dir / "i22-977723.nxs"
    if transmission_reference_file is None:
        transmission_reference = background
    else:
        transmission_reference = Path(transmission_reference_file)
        if not transmission_reference.is_absolute():
            transmission_reference = project_dir / transmission_reference
    pipelines = {
        "SAXS": project_dir / "pipelines" / "I22_SAXS_solids_operando.yaml",
        "WAXS": project_dir / "pipelines" / WAXS_PIPELINES[beamline_configuration],
    }
    calibrations = {
        detector: data_dir / "processing" / f"{detector}_calibration.nxs"
        for detector in DETECTOR_DATASETS
    }
    masks = {detector: data_dir / "processing" / f"{detector}_mask.nxs" for detector in DETECTOR_DATASETS}
    required = [background, transmission_reference, *pipelines.values(), *calibrations.values(), *masks.values()]
    missing = [path for path in required if not path.is_file()]
    if missing:
        raise FileNotFoundError("Missing required I22 inputs: " + ", ".join(str(path) for path in missing))

    def sample_candidate(path: Path) -> bool:
        if not path.is_file() or path.resolve() == background.resolve():
            return False
        return all(
            (data_dir / f"{path.stem}-{suffix}.h5").is_file()
            for suffix in ("Pilatus2M_SAXS", "Pilatus2M_WAXS", "bsdiodes", "I0")
        )

    samples = tuple(sorted(path.resolve() for path in data_dir.glob(sample_glob) if sample_candidate(path)))
    if not samples:
        raise FileNotFoundError(f"No complete I22 samples matching {sample_glob!r} were found below {data_dir}.")
    for detector in DETECTOR_DATASETS:
        with h5py.File(calibrations[detector], "r") as calibration, h5py.File(masks[detector], "r") as mask:
            calibration_shape = tuple(calibration["/entry1/calibration_data/data"].shape)
            mask_shape = tuple(mask["/entry/mask/mask"].shape)
            if calibration_shape != mask_shape:
                raise ValueError(f"{detector} calibration/mask shapes differ: {calibration_shape} and {mask_shape}.")

    preprocessed_dir = project_dir / "work" / "preprocessed"
    prepared = {
        source: preprocess_measurement(
            source,
            preprocessed_dir,
            transmission_reference_file=transmission_reference,
            absolute_intensity_factor=absolute_intensity_factor,
            overwrite=overwrite,
        )
        for source in (*samples, background.resolve())
    }
    return I22Inputs(
        project_dir=project_dir,
        beamline_configuration=beamline_configuration,
        pipeline_paths=pipelines,
        calibration_files=calibrations,
        mask_files=masks,
        background_file=background.resolve(),
        transmission_reference_file=transmission_reference.resolve(),
        sample_files=samples,
        preprocessed_samples=tuple(prepared[path] for path in samples),
        preprocessed_background=prepared[background.resolve()],
    )


def source_registrations(inputs: I22Inputs, *, sample: str | Path | None = None) -> list[dict[str, Any]]:
    registrations = [
        {"ref": "background", "type": "hdf", "location": str(inputs.preprocessed_background)},
        {"ref": "saxs_calibration", "type": "hdf", "location": str(inputs.calibration_files["SAXS"])},
        {"ref": "saxs_mask", "type": "hdf", "location": str(inputs.mask_files["SAXS"])},
        {"ref": "waxs_calibration", "type": "hdf", "location": str(inputs.calibration_files["WAXS"])},
        {"ref": "waxs_mask", "type": "hdf", "location": str(inputs.mask_files["WAXS"])},
    ]
    if sample is not None:
        registrations.append({"ref": "sample", "type": "hdf", "location": str(sample)})
    return registrations


def validation_pipeline_yaml(pipeline_path: str | Path) -> str:
    """Remove display/file sinks from a copy while preserving numerical processing."""

    pipeline = yaml.safe_load(Path(pipeline_path).read_text(encoding="utf-8"))
    for step_id in ("PL_IQ", "SV_IQ", "PL_2D", "SV_2D"):
        pipeline["steps"].pop(step_id, None)
    pipeline["steps"]["AV"]["requires_steps"] = ["PO"]
    pipeline["name"] = f"{pipeline['name']} - chunk validation"
    pipeline["description"] = (
        f"{pipeline.get('description', '')} Test-only copy without visualization or intermediate sinks."
    ).strip()
    return yaml.safe_dump(pipeline, sort_keys=False)


def sample_aligned_paths(detector: str) -> tuple[str, ...]:
    return (
        DETECTOR_DATASETS[detector],
        "/modacor/normalization/i0_channel_1_mean",
        "/modacor/normalization/i0_channel_1_sem",
        "/entry1/sample/transmission",
        "/entry1/sample/transmission_sem",
        f"/modacor/normalization/{detector.lower()}_count_time",
    )


def upload_sample_chunk(
    buffer: SourceBufferClient, detector: str, source_path: str | Path, start: int, stop: int
) -> None:
    """Upload one detector slice and its aligned normalization inputs."""

    selection = (slice(None), slice(start, stop), slice(None), slice(None))
    with h5py.File(source_path, "r") as source:
        for data_path in sample_aligned_paths(detector):
            buffer.put_array(data_path, source[data_path][selection])
        scalar_path = "/modacor/calibration/absolute_intensity_factor"
        buffer.put_array(scalar_path, source[scalar_path][()])
        for data_path in (
            "/modacor/normalization/i0_channel_1_mean",
            "/modacor/normalization/i0_channel_1_sem",
            "/entry1/sample/transmission",
            "/entry1/sample/transmission_sem",
            f"/modacor/normalization/{detector.lower()}_count_time",
            scalar_path,
        ):
            buffer.put_attrs(data_path, {"units": str(_decode(source[data_path].attrs["units"]))})


def chunk_work_items(
    measurements: tuple[tuple[Path, Path], ...],
    *,
    frame_count: int,
    chunk_size: int,
) -> tuple[ChunkWorkItem, ...]:
    _validate_chunk_grid(measurements, frame_count=frame_count, chunk_size=chunk_size)
    return tuple(
        ChunkWorkItem(measurement_index, chunk_index, master, source, start, min(start + chunk_size, frame_count))
        for measurement_index, (master, source) in enumerate(measurements)
        for chunk_index, start in enumerate(range(0, frame_count, chunk_size))
    )


def validate_chunk_sources(
    measurements: tuple[tuple[Path, Path], ...], detector: str, *, frame_count: int
) -> tuple[int, ...]:
    if detector not in DETECTOR_DATASETS:
        raise ValueError(f"Unknown detector {detector!r}; expected one of {tuple(DETECTOR_DATASETS)}.")
    _validate_chunk_grid(measurements, frame_count=frame_count, chunk_size=1)
    shapes = []
    for _master, source_path in measurements:
        with h5py.File(source_path, "r") as source:
            shapes.append(tuple(source[DETECTOR_DATASETS[detector]].shape))
    if len(set(shapes)) != 1:
        raise ValueError(f"{detector} detector shapes differ: {shapes}.")
    if len(shapes[0]) < 2:
        raise ValueError(f"{detector} detector source must have at least two dimensions; got {shapes[0]}.")
    if frame_count > shapes[0][1]:
        raise ValueError(f"{detector} requested {frame_count} frames, but the source has {shapes[0][1]}.")
    return shapes[0]


def _validate_chunk_grid(
    measurements: tuple[tuple[Path, Path], ...], *, frame_count: int, chunk_size: int
) -> None:
    if not measurements:
        raise ValueError("measurements must not be empty.")
    if isinstance(frame_count, bool) or not isinstance(frame_count, int) or frame_count <= 0:
        raise ValueError("frame_count must be a positive integer.")
    if isinstance(chunk_size, bool) or not isinstance(chunk_size, int) or chunk_size <= 0:
        raise ValueError("chunk_size must be a positive integer.")


def _pilot_output_layout(
    pilot_path: str | Path,
    pilot_run_name: str,
    *,
    measurement_count: int,
    chunks_per_measurement: int,
) -> ChunkOutputLayout:
    with h5py.File(pilot_path, "r") as pilot:
        group = pilot[f"/processing/result/{pilot_run_name}/sample/signal"]
        signal = group["signal"]
        chunk_result_shape = tuple(signal.shape)
        final_shape = (measurement_count, chunks_per_measurement, *chunk_result_shape)
        arrays = [ChunkArrayLayout("signal", final_shape, signal.dtype.str)]
        if "weights" in group:
            weights = group["weights"]
            if tuple(weights.shape) != chunk_result_shape:
                raise ValueError(
                    f"Pilot weights shape {weights.shape} does not match signal shape {chunk_result_shape}."
                )
            arrays.append(ChunkArrayLayout("weights", final_shape, weights.dtype.str))
        elif float(group.attrs.get("weight_scalar", 1.0)) != 1.0:
            weight = np.asarray(group.attrs["weight_scalar"])
            arrays.append(ChunkArrayLayout("weights", (), weight.dtype.str, PlacementBinding(kind="static")))
        if "uncertainties" in group:
            for name, dataset in group["uncertainties"].items():
                if tuple(dataset.shape) != chunk_result_shape:
                    raise ValueError(
                        f"Pilot uncertainty {name!r} shape {dataset.shape} does not match signal shape "
                        f"{chunk_result_shape}."
                    )
                arrays.append(ChunkArrayLayout(f"uncertainties/{name}", final_shape, dataset.dtype.str))
        axis_names = tuple(str(_decode(value)) for value in group.attrs.get("axes", ()))
        for axis_name in dict.fromkeys(name for name in axis_names if name != "."):
            axis = group[axis_name]
            arrays.append(
                ChunkArrayLayout(
                    f"axes/{axis_name}",
                    tuple(axis.shape),
                    axis.dtype.str,
                    PlacementBinding(kind="static"),
                    units=str(_decode(axis.attrs["units"])),
                    rank_of_data=int(axis.attrs["rank_of_data"]),
                )
            )
        return ChunkOutputLayout(
            output_id="sample_signal",
            processing_path="/sample/signal",
            destination_path="sample/signal",
            units=str(_decode(signal.attrs["units"])),
            rank_of_data=int(signal.attrs["rank_of_data"]),
            arrays=tuple(arrays),
            axis_names=(".", ".", *axis_names),
        )


def build_complete_plan(
    pilot_path: str | Path,
    pilot_run_name: str,
    detector: str,
    measurements: tuple[tuple[Path, Path], ...],
    source_shape: tuple[int, ...],
    *,
    frame_count: int,
    chunk_size: int,
    source_mode: str,
) -> ChunkPlan:
    """Build I22's explicit plan for outputs that reduce both input batch axes."""

    if source_mode not in {"buffer", "hdf", "tiled"}:
        raise ValueError("source_mode must be 'buffer', 'hdf', or 'tiled'.")
    if detector not in DETECTOR_DATASETS:
        raise ValueError(f"Unknown detector {detector!r}; expected one of {tuple(DETECTOR_DATASETS)}.")
    _validate_chunk_grid(measurements, frame_count=frame_count, chunk_size=chunk_size)
    if len(source_shape) < 2:
        raise ValueError(f"source_shape must have at least two dimensions; got {source_shape}.")
    if frame_count > source_shape[1]:
        raise ValueError(f"Requested {frame_count} frames, but source_shape contains {source_shape[1]}.")
    chunks_per_measurement = (frame_count + chunk_size - 1) // chunk_size
    output = _pilot_output_layout(
        pilot_path,
        pilot_run_name,
        measurement_count=len(measurements),
        chunks_per_measurement=chunks_per_measurement,
    )
    work = chunk_work_items(measurements, frame_count=frame_count, chunk_size=chunk_size)
    driver: dict[str, Any] = {
        "source_dataset": DETECTOR_DATASETS[detector],
        "full_shape": list(source_shape),
        "measurement_files": [str(master) for master, _source in measurements],
        "source_mode": source_mode,
    }
    source_bindings = ()
    if source_mode in {"hdf", "tiled"}:
        driver["source"] = f"sample::{DETECTOR_DATASETS[detector]}"
        source_bindings = tuple(ChunkSourceBinding("sample", path, "aligned") for path in sample_aligned_paths(detector))
    suffix = "" if source_mode == "buffer" else f"-{source_mode}"
    return ChunkPlan(
        schema_version="1.0",
        plan_id=f"i22-{detector.lower()}-{len(measurements)}m-{frame_count}f-{chunk_size}f{suffix}-chunks",
        total_chunks=len(work),
        expected_chunk_ids=tuple(f"m{item.measurement_index:03d}-c{item.chunk_index:03d}" for item in work),
        outputs=(output,),
        driver=driver,
        batch_axes=(0, 1),
        data_axes=(2, 3),
        axis_rules=({"axis": 1, "start": 0, "stop": frame_count, "stride": 1, "chunk_size": chunk_size},),
        bindings=(
            {"source": "sample detector and normalization arrays", "role": "aligned"},
            {"source": "background, calibration, and masks", "role": "static"},
        ),
        source_bindings=source_bindings,
    )


def chunk_spec(plan: ChunkPlan, item: ChunkWorkItem) -> ChunkSpec:
    output = plan.output("sample_signal")
    source_selection = (
        AxisSelector.all(),
        AxisSelector.sliced(item.start, item.stop),
        AxisSelector.all(),
        AxisSelector.all(),
    )
    destination_selection = (
        AxisSelector.index(item.measurement_index),
        AxisSelector.index(item.chunk_index),
        *(AxisSelector.all() for _ in output.signal.final_shape[2:]),
    )
    expected_input_shape = list(plan.driver["full_shape"])
    expected_input_shape[1] = item.stop - item.start
    ordinal = plan.expected_chunk_ids.index(f"m{item.measurement_index:03d}-c{item.chunk_index:03d}")
    return ChunkSpec(
        schema_version=plan.schema_version,
        plan_id=plan.plan_id,
        plan_hash=plan.plan_hash,
        chunk_id=plan.expected_chunk_ids[ordinal],
        ordinal=ordinal,
        grid_index=(item.measurement_index, item.chunk_index),
        source_selection=source_selection,
        expected_input_shape=tuple(expected_input_shape),
        placements=(
            ChunkPlacement(
                output_id=output.output_id,
                destination_selection=destination_selection,
                expected_shape=tuple(output.signal.final_shape[2:]),
            ),
        ),
    )


def direct_sample_registration(source_mode: str, source_path: str | Path, *, tiled_url: str | None = None):
    source_path = Path(source_path)
    if source_mode == "hdf":
        return {"ref": "sample", "type": "hdf", "location": str(source_path)}
    if source_mode == "tiled" and tiled_url:
        return {
            "ref": "sample",
            "type": "tiled",
            "location": tiled_url,
            "kwargs": {"base_item_path": f"samples/{source_path.stem}"},
        }
    raise ValueError("Tiled registrations require tiled_url; source_mode must be 'hdf' or 'tiled'.")


def compare_run_groups(
    left_path: str | Path,
    left_run: str,
    right_path: str | Path,
    right_run: str,
    *,
    measurement_count: int,
    chunks_per_measurement: int,
    rtol: float = 1e-12,
) -> dict[str, Any]:
    """Compare stored results one logical chunk at a time."""

    with h5py.File(left_path, "r") as left_h5, h5py.File(right_path, "r") as right_h5:
        roots = [h5[f"/processing/result/{run}"] for h5, run in ((left_h5, left_run), (right_h5, right_run))]
        datasets: list[dict[str, h5py.Dataset]] = []
        for root in roots:
            found: dict[str, h5py.Dataset] = {}

            def collect_dataset(name: str, item: Any) -> None:
                if isinstance(item, h5py.Dataset):
                    found[name] = item

            root.visititems(collect_dataset)
            datasets.append(found)
        if set(datasets[0]) != set(datasets[1]):
            raise AssertionError("Stored result dataset paths differ.")
        compared = 0
        for relative_path in sorted(datasets[0]):
            left, right = datasets[0][relative_path], datasets[1][relative_path]
            if left.shape != right.shape or left.dtype != right.dtype:
                raise AssertionError(f"{relative_path} schema differs: {left.shape}/{left.dtype} vs {right.shape}/{right.dtype}.")
            selections = (
                ((measurement, chunk, ...) for measurement in range(measurement_count) for chunk in range(chunks_per_measurement))
                if left.ndim >= 2 and tuple(left.shape[:2]) == (measurement_count, chunks_per_measurement)
                else ((),)
            )
            for selection in selections:
                if np.issubdtype(left.dtype, np.number):
                    np.testing.assert_allclose(left[selection], right[selection], rtol=rtol, atol=0, equal_nan=True)
                else:
                    np.testing.assert_array_equal(left[selection], right[selection])
                compared += 1
    return {"datasets": len(datasets[0]), "compared_slices": compared, "status": "allclose"}


def trace_source_slices(output_path: str | Path, plan: ChunkPlan) -> set[str]:
    with h5py.File(output_path, "r") as result:
        first = result[
            f"/processing/chunk_plans/{plan.plan_id}/chunks/{plan.expected_chunk_ids[0]}/execution_json"
        ][()]
    execution = json.loads(str(_decode(first)))
    return {item["data_key"] for item in execution["source_slices"]}
