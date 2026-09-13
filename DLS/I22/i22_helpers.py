"""I22-specific data preparation and chunk-plan helpers for the notebooks."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import h5py
import numpy as np
import yaml

from modacor.client import BufferClient
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
PREPROCESSING_VERSION = "2026-09-02-i22-normalization-v3"


@dataclass(frozen=True, slots=True)
class I22Inputs:
    project_dir: Path
    pipeline_paths: dict[str, Path]
    calibration_files: dict[str, Path]
    mask_files: dict[str, Path]
    background_file: Path
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


def _needs_rewrite(output_file: Path, *, overwrite: bool) -> bool:
    if overwrite or not output_file.exists():
        return True
    try:
        with h5py.File(output_file, "r") as h5:
            return _decode(h5.attrs.get("preprocessing_version", "")) != PREPROCESSING_VERSION
    except OSError:
        return True


def preprocess_measurement(
    master_file: str | Path,
    output_dir: str | Path,
    *,
    diode_channel: int = 1,
    absolute_intensity_factor: float = 3.8e-15,
    overwrite: bool = False,
) -> Path:
    """Reshape frame metadata while leaving detector images in their original files."""

    master_file = Path(master_file).resolve()
    output_file = Path(output_dir) / f"{master_file.stem}_modacor.nxs"
    if not _needs_rewrite(output_file, overwrite=overwrite):
        return output_file

    with h5py.File(master_file, "r") as source:
        diode_values = np.asarray(source["/entry1/bsdiodes/data"][()], dtype=float)
        if diode_values.ndim != 4 or diode_channel >= diode_values.shape[-1]:
            raise ValueError(f"Unexpected diode shape {diode_values.shape}; channel {diode_channel} is unavailable.")
        detector_shapes = {name: tuple(source[path].shape) for name, path in DETECTOR_DATASETS.items()}
        leading_shape = detector_shapes["SAXS"][:-2]
        if detector_shapes["WAXS"][:-2] != leading_shape or tuple(diode_values.shape[:2]) != leading_shape:
            raise ValueError(f"Incompatible I22 leading dimensions: detectors={detector_shapes}, diode={diode_values.shape}.")

        samples = diode_values[..., diode_channel]
        valid_count = np.sum(np.isfinite(samples), axis=-1).astype(np.int32)
        diode_mean = np.nanmean(samples, axis=-1)
        diode_std = np.nanstd(samples, axis=-1, ddof=1)
        diode_sem = diode_std / np.sqrt(valid_count)
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
        transmission_path = "/entry1/I0/transmission"
        transmission = _frame_array(source[transmission_path][()], leading_shape, name=transmission_path)

    output_file.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_file.with_suffix(output_file.suffix + ".tmp")
    with h5py.File(temporary, "w") as target:
        relative_master = os.path.relpath(master_file, start=output_file.parent)
        target["entry1"] = h5py.ExternalLink(relative_master, "/entry1")
        target.attrs.update(
            creator="I22 MoDaCor examples helper",
            source_file=relative_master,
            preprocessing_version=PREPROCESSING_VERSION,
        )
        normalization = target.require_group("/modacor/normalization")
        normalization.attrs.update(
            description="Frame-wise arrays reshaped for broadcasting over detector y/x axes.",
            frame_shape=leading_shape,
            bsdiodes_source="/entry1/bsdiodes/data",
            bsdiodes_reduction_axis=2,
            bsdiodes_channel_index=diode_channel,
        )
        calibration = target.require_group("/modacor/calibration")
        calibration.attrs["description"] = "Scalar calibration values used by the I22 MoDaCor pipelines."
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
        _write_dataset(normalization, "bsdiodes_channel_1_n_valid", _detector_divisor(valid_count))
        _write_dataset(normalization, "transmission", _detector_divisor(transmission), units="dimensionless")
        for detector, (values, units) in count_times.items():
            _write_dataset(normalization, f"{detector.lower()}_count_time", _detector_divisor(values), units=units)
    temporary.replace(output_file)
    return output_file


def prepare_inputs(
    project_dir: str | Path,
    *,
    sample_glob: str = "i22-978???.nxs",
    diode_channel: int = 1,
    absolute_intensity_factor: float = 3.8e-15,
    overwrite: bool = False,
) -> I22Inputs:
    """Discover, validate, and minimally preprocess the packaged I22 inputs."""

    project_dir = Path(project_dir).resolve()
    data_dir = project_dir / "data"
    background = data_dir / "i22-977723.nxs"
    pipelines = {
        detector: project_dir / "pipelines" / f"I22_{detector}_solids_operando.yaml"
        for detector in DETECTOR_DATASETS
    }
    calibrations = {
        detector: data_dir / "processing" / f"{detector}_calibration.nxs"
        for detector in DETECTOR_DATASETS
    }
    masks = {detector: data_dir / "processing" / f"{detector}_mask.nxs" for detector in DETECTOR_DATASETS}
    required = [background, *pipelines.values(), *calibrations.values(), *masks.values()]
    missing = [path for path in required if not path.is_file()]
    if missing:
        raise FileNotFoundError("Missing required I22 inputs: " + ", ".join(str(path) for path in missing))

    def sample_candidate(path: Path) -> bool:
        if not path.is_file() or path.resolve() == background.resolve():
            return False
        return all(
            (data_dir / f"{path.stem}-{suffix}.h5").is_file()
            for suffix in ("Pilatus2M_SAXS", "Pilatus2M_WAXS", "bsdiodes")
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
            diode_channel=diode_channel,
            absolute_intensity_factor=absolute_intensity_factor,
            overwrite=overwrite,
        )
        for source in (*samples, background.resolve())
    }
    return I22Inputs(
        project_dir=project_dir,
        pipeline_paths=pipelines,
        calibration_files=calibrations,
        mask_files=masks,
        background_file=background.resolve(),
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
        "/modacor/normalization/bsdiodes_channel_1_mean",
        "/modacor/normalization/bsdiodes_channel_1_std",
        f"/modacor/normalization/{detector.lower()}_count_time",
    )


def upload_sample_chunk(buffer: BufferClient, detector: str, source_path: str | Path, start: int, stop: int) -> None:
    """Upload one detector slice and its aligned normalization inputs."""

    selection = (slice(None), slice(start, stop), slice(None), slice(None))
    with h5py.File(source_path, "r") as source:
        for data_path in sample_aligned_paths(detector):
            buffer.put_array(data_path, source[data_path][selection])
        scalar_path = "/modacor/calibration/absolute_intensity_factor"
        buffer.put_array(scalar_path, source[scalar_path][()])
        for data_path in (
            "/modacor/normalization/bsdiodes_channel_1_mean",
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
    return tuple(
        ChunkWorkItem(measurement_index, chunk_index, master, source, start, min(start + chunk_size, frame_count))
        for measurement_index, (master, source) in enumerate(measurements)
        for chunk_index, start in enumerate(range(0, frame_count, chunk_size))
    )


def validate_chunk_sources(
    measurements: tuple[tuple[Path, Path], ...], detector: str, *, frame_count: int
) -> tuple[int, ...]:
    shapes = []
    for _master, source_path in measurements:
        with h5py.File(source_path, "r") as source:
            shapes.append(tuple(source[DETECTOR_DATASETS[detector]].shape))
    if len(set(shapes)) != 1:
        raise ValueError(f"{detector} detector shapes differ: {shapes}.")
    if frame_count > shapes[0][1]:
        raise ValueError(f"{detector} requested {frame_count} frames, but the source has {shapes[0][1]}.")
    return shapes[0]


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
    chunks_per_measurement = int(np.ceil(frame_count / chunk_size))
    with h5py.File(pilot_path, "r") as pilot:
        group = pilot[f"/processing/result/{pilot_run_name}/sample/signal"]
        signal = group["signal"]
        chunk_result_shape = tuple(signal.shape)
        final_shape = (len(measurements), chunks_per_measurement, *chunk_result_shape)
        arrays = [ChunkArrayLayout("signal", final_shape, signal.dtype.str)]
        if "weights" in group:
            weights = group["weights"]
            arrays.append(ChunkArrayLayout("weights", final_shape, weights.dtype.str))
        elif float(group.attrs.get("weight_scalar", 1.0)) != 1.0:
            weight = np.asarray(group.attrs["weight_scalar"])
            arrays.append(ChunkArrayLayout("weights", (), weight.dtype.str, PlacementBinding(kind="static")))
        if "uncertainties" in group:
            arrays.extend(
                ChunkArrayLayout(f"uncertainties/{name}", final_shape, dataset.dtype.str)
                for name, dataset in group["uncertainties"].items()
            )
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
        output = ChunkOutputLayout(
            output_id="sample_signal",
            processing_path="/sample/signal",
            destination_path="sample/signal",
            units=str(_decode(signal.attrs["units"])),
            rank_of_data=int(signal.attrs["rank_of_data"]),
            arrays=tuple(arrays),
            axis_names=(".", ".", *axis_names),
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
