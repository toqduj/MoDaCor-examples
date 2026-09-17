"""Small orchestration helpers for the B21 chunked pre-filter notebook."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

import h5py
import numpy as np

from modacor.io.chunking import ChunkAxisRule, ProvisionalChunkOutput, ProvisionalChunkPlan


PREFILTER_SIGNAL_PATH = "/processing/result/measurement/prefilter/signal/signal"
PREFILTER_Q_PATH = "/processing/result/measurement/prefilter/Q/signal"
QUALITY_ROOT = "/processing/result/quality/prefilter"
QUALITY_PATHS = {
    "signal": f"{QUALITY_ROOT}/signal/signal",
    "Q": f"{QUALITY_ROOT}/Q/signal",
    "flags": f"{QUALITY_ROOT}/frame_quality_flags/signal",
    "high_q_total": f"{QUALITY_ROOT}/high_q_total/signal",
    "low_q_total": f"{QUALITY_ROOT}/low_q_total/signal",
    "high_q_reference": f"{QUALITY_ROOT}/high_q_reference/signal",
    "low_q_reference": f"{QUALITY_ROOT}/low_q_reference/signal",
}
QUALITY_DATA_PATHS = [
    "/prefilter/signal",
    "/prefilter/Q",
    "/prefilter/frame_quality_flags",
    "/prefilter/high_q_total",
    "/prefilter/low_q_total",
    "/prefilter/high_q_reference",
    "/prefilter/low_q_reference",
]


def _has_datasets(path: Path, required: tuple[str, ...]) -> bool:
    if not path.is_file():
        return False
    try:
        with h5py.File(path, "r") as handle:
            return all(item in handle for item in required)
    except OSError:
        return False


def prefilter_output_complete(path: Path, frame_shape: tuple[int, ...]) -> bool:
    if not _has_datasets(path, (PREFILTER_SIGNAL_PATH, PREFILTER_Q_PATH)):
        return False
    with h5py.File(path, "r") as handle:
        signal_shape = tuple(handle[PREFILTER_SIGNAL_PATH].shape)
        q_shape = tuple(handle[PREFILTER_Q_PATH].shape)
    return signal_shape[:-1] == frame_shape and q_shape == signal_shape


def quality_output_complete(path: Path, frame_shape: tuple[int, ...]) -> bool:
    if not _has_datasets(path, tuple(QUALITY_PATHS.values())):
        return False
    with h5py.File(path, "r") as handle:
        signal_shape = tuple(handle[QUALITY_PATHS["signal"]].shape)
        flags_shape = tuple(handle[QUALITY_PATHS["flags"]].shape)
    return signal_shape[:-1] == frame_shape and flags_shape == frame_shape


def prefilter_plan(run_name: str, chunk_size: int) -> ProvisionalChunkPlan:
    return ProvisionalChunkPlan(
        schema_version="1.0",
        plan_id=f"{run_name}-prefilter-v1",
        driver={
            "source": "sample::/entry1/instrument/detector/data",
            "rank_of_data": 2,
        },
        axis_rules=(ChunkAxisRule(axis=1, chunk_size=chunk_size),),
        outputs=(
            ProvisionalChunkOutput("signal", "/prefilter/signal", "prefilter/signal"),
            ProvisionalChunkOutput("Q", "/prefilter/Q", "prefilter/Q"),
        ),
    )


def process_prefilter_measurement(
    *,
    client: Any,
    session: Any,
    run: dict[str, Any],
    output_path: Path,
    chunk_size: int,
    first_mode: str,
) -> dict[str, Any]:
    frame_shape = tuple(run["detector_shape"][:-2])
    if prefilter_output_complete(output_path, frame_shape):
        return {
            "run": run["run"],
            "title": run["title"],
            "path": output_path,
            "cached": True,
            "chunks": 0,
        }
    if output_path.exists():
        raise FileExistsError(
            f"Incomplete pre-filter output already exists: {output_path}. "
            "Archive or remove that generated work file before retrying."
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    session.register_source(
        {"ref": "sample", "type": "hdf", "location": str(Path(run["master_path"]).resolve())}
    )
    session.register_sink(
        {"ref": "prefilter", "type": "hdf_chunked", "location": str(output_path.resolve())}
    )
    output = client.chunked_outputs.create_provisional(
        session=session,
        sink_ref="prefilter",
        subpath="measurement",
        plan=prefilter_plan(run["run"], chunk_size),
    )
    expected_chunks = int(output.creation["expected_chunks"])
    final_hash = None
    try:
        for ordinal in range(expected_chunks):
            result = session.process(
                mode=first_mode if ordinal == 0 else "partial",
                rollback_snapshot=False,
                chunk_output=output.chunk_id(f"c{ordinal:06d}"),
            )
            final_hash = result["chunk_output"]["plan_hash"]
        finalized = output.finalize(plan_hash=final_hash)
        if finalized["status"] != "complete":
            raise RuntimeError(f"Chunked pre-filter did not finalize: {finalized}")
    finally:
        output.detach()

    if not prefilter_output_complete(output_path, frame_shape):
        raise RuntimeError(f"Finalized pre-filter output failed validation: {output_path}")
    return {
        "run": run["run"],
        "title": run["title"],
        "path": output_path,
        "cached": False,
        "chunks": expected_chunks,
    }


def process_quality_measurement(
    *,
    session: Any,
    run: dict[str, Any],
    prefilter_path: Path,
    output_path: Path,
) -> dict[str, Any]:
    frame_shape = tuple(run["detector_shape"][:-2])
    if quality_output_complete(output_path, frame_shape):
        cached = True
    else:
        if output_path.exists():
            raise FileExistsError(
                f"Incomplete quality output already exists: {output_path}. "
                "Archive or remove that generated work file before retrying."
            )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        session.register_source(
            {"ref": "curves", "type": "hdf", "location": str(prefilter_path.resolve())}
        )
        result = session.process(
            mode="full",
            changed_sources=["curves"],
            run_name="quality",
            rollback_snapshot=False,
            write_hdf={"path": str(output_path.resolve()), "data_paths": QUALITY_DATA_PATHS},
        )
        if result["status"] != "succeeded":
            raise RuntimeError(f"B21 quality pass failed: {result}")
        cached = False

    if not quality_output_complete(output_path, frame_shape):
        raise RuntimeError(f"B21 quality output failed validation: {output_path}")
    arrays = load_quality_arrays(output_path)
    counts = Counter(int(value) for value in arrays["flags"].reshape(-1))
    return {
        "run": run["run"],
        "title": run["title"],
        "role": run["role"],
        "path": output_path,
        "cached": cached,
        "accepted": counts[0],
        "rejected": int(arrays["flags"].size - counts[0]),
        "flag_counts": {str(flag): counts[flag] for flag in sorted(counts)},
    }


def load_quality_arrays(path: Path) -> dict[str, np.ndarray]:
    with h5py.File(path, "r") as handle:
        return {name: np.asarray(handle[data_path]) for name, data_path in QUALITY_PATHS.items()}


def compare_buffer_measurements(
    initial_path: Path,
    final_path: Path,
    *,
    relative_limit: float = 0.01,
) -> dict[str, Any]:
    """Compare accepted initial/final buffer curves on a relative and SEM scale."""

    if not 0.0 < relative_limit < 1.0:
        raise ValueError("relative_limit must satisfy 0 < value < 1")

    initial = load_quality_arrays(initial_path)
    final = load_quality_arrays(final_path)

    def accepted_curves(values: dict[str, np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
        signal = np.asarray(values["signal"], dtype=float)
        q_values = np.broadcast_to(np.asarray(values["Q"], dtype=float), signal.shape)
        curves = signal.reshape(-1, signal.shape[-1])
        q_curves = q_values.reshape(curves.shape)
        accepted = np.asarray(values["flags"], dtype=np.uint32).reshape(-1) == 0
        if not np.any(accepted):
            raise ValueError("A buffer comparison requires at least one accepted frame")
        return curves[accepted], q_curves[accepted]

    initial_curves, initial_q = accepted_curves(initial)
    final_curves, final_q = accepted_curves(final)
    if initial_curves.shape[-1] != final_curves.shape[-1]:
        raise ValueError("Initial and final buffer curves use different bin counts")

    def mean_and_sem(curves: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        mean = np.nanmean(curves, axis=0)
        if curves.shape[0] < 2:
            return mean, np.full(mean.shape, np.nan, dtype=float)
        sem = np.nanstd(curves, axis=0, ddof=1) / np.sqrt(curves.shape[0])
        return mean, sem

    initial_mean, initial_sem = mean_and_sem(initial_curves)
    final_mean, final_sem = mean_and_sem(final_curves)
    initial_q_mean = np.nanmean(initial_q, axis=0)
    final_q_mean = np.nanmean(final_q, axis=0)
    q = 0.5 * (initial_q_mean + final_q_mean)

    valid = (
        np.isfinite(q)
        & np.isfinite(initial_mean)
        & np.isfinite(final_mean)
        & (initial_mean > 0.0)
        & (final_mean > 0.0)
    )
    if not np.any(valid):
        raise ValueError("The initial/final buffers have no common finite positive bins")

    log_ratio = np.full(q.shape, np.nan, dtype=float)
    log_ratio[valid] = np.log(final_mean[valid] / initial_mean[valid])
    mean_log_ratio = float(np.mean(log_ratio[valid]))
    curve_distance = float(np.expm1(np.sqrt(np.mean(np.square(log_ratio[valid])))))
    scale_ratio = float(np.exp(mean_log_ratio))
    shape_distance = float(
        np.expm1(np.sqrt(np.mean(np.square(log_ratio[valid] - mean_log_ratio))))
    )

    symmetric_relative = np.full(q.shape, np.nan, dtype=float)
    symmetric_relative[valid] = (
        2.0
        * (final_mean[valid] - initial_mean[valid])
        / (np.abs(final_mean[valid]) + np.abs(initial_mean[valid]))
    )
    combined_sem = np.sqrt(np.square(initial_sem) + np.square(final_sem))
    z_score = np.full(q.shape, np.nan, dtype=float)
    valid_z = valid & np.isfinite(combined_sem) & (combined_sem > 0.0)
    z_score[valid_z] = (final_mean[valid_z] - initial_mean[valid_z]) / combined_sem[valid_z]

    q_mismatch = np.abs(final_q_mean - initial_q_mean)
    q_scale = np.maximum(np.abs(q), np.finfo(float).tiny)
    standardized_rms = (
        float(np.sqrt(np.mean(np.square(z_score[valid_z])))) if np.any(valid_z) else None
    )
    maximum_absolute_z = float(np.max(np.abs(z_score[valid_z]))) if np.any(valid_z) else None
    metrics = {
        "relative_limit": float(relative_limit),
        "within_relative_limit": bool(curve_distance <= relative_limit),
        "curve_log_rms_distance": curve_distance,
        "multiplicative_scale_final_over_initial": scale_ratio,
        "shape_log_rms_distance_after_scaling": shape_distance,
        "maximum_absolute_bin_difference": float(np.nanmax(np.abs(symmetric_relative))),
        "all_bins_within_relative_limit": bool(
            np.nanmax(np.abs(symmetric_relative)) <= relative_limit
        ),
        "standardized_rms_distance": standardized_rms,
        "maximum_absolute_z_score": maximum_absolute_z,
        "initial_accepted_frames": int(initial_curves.shape[0]),
        "final_accepted_frames": int(final_curves.shape[0]),
        "compared_bins": int(np.count_nonzero(valid)),
        "maximum_relative_q_mismatch": float(np.nanmax(q_mismatch[valid] / q_scale[valid])),
    }
    return {
        "metrics": metrics,
        "q": q,
        "initial_mean": initial_mean,
        "final_mean": final_mean,
        "initial_sem": initial_sem,
        "final_sem": final_sem,
        "symmetric_relative_difference": symmetric_relative,
        "z_score": z_score,
        "valid": valid,
    }
