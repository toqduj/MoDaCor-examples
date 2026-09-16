"""Reproducible performance measurements for the I22 server workflows.

The benchmark deliberately starts after :func:`prepare_inputs`: conversion of
incompatible beamline metadata is setup work, not part of a pipeline rerun.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any, Iterable

import h5py
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import psutil

import modacor

from i22_helpers import (
    build_complete_plan,
    chunk_spec,
    chunk_work_items,
    direct_sample_registration,
    source_registrations,
    upload_sample_chunk,
    validate_chunk_sources,
    validation_pipeline_yaml,
)

DETECTOR_PATHS = {
    "SAXS": "/entry1/detector/data",
    "WAXS": "/entry1/Pilatus2M_WAXS/data",
}


@dataclass(frozen=True, slots=True)
class BenchmarkSettings:
    """Controls for a complete or reduced benchmark run."""

    detectors: tuple[str, ...] = ("SAXS", "WAXS")
    measurement_limit: int = 4
    full_repeats: int = 3
    partial_repeats: int = 6
    frame_count: int = 100
    chunk_size: int = 10
    compression: str = "gzip"
    compression_level: int = 1
    write_server_hdf: bool = True


def new_run_dir(project_dir: str | Path, label: str | None = None) -> Path:
    """Create a unique result directory without overwriting older measurements."""

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    suffix = f"-{label}" if label else ""
    result = (
        Path(project_dir)
        / "work"
        / "supplementary"
        / "poster_2026"
        / "performance"
        / f"{timestamp}{suffix}"
    )
    result.mkdir(parents=True, exist_ok=False)
    return result


def _cpu_name() -> str:
    if sys.platform == "darwin":
        result = subprocess.run(
            ["sysctl", "-n", "machdep.cpu.brand_string"],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    return platform.processor() or platform.machine()


def system_metadata(settings: BenchmarkSettings, inputs: Any) -> dict[str, Any]:
    """Capture enough context for a defensible poster caption."""

    shapes: dict[str, list[int]] = {}
    sample = inputs.measurements[0][1]
    with h5py.File(sample, "r") as source:
        for detector in settings.detectors:
            shapes[detector] = list(source[DETECTOR_PATHS[detector]].shape)
    return {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "host": platform.node(),
        "platform": platform.platform(),
        "cpu": _cpu_name(),
        "logical_cpu_count": os.cpu_count(),
        "physical_cpu_count": psutil.cpu_count(logical=False),
        "memory_gib": round(psutil.virtual_memory().total / 2**30, 2),
        "python": sys.version.split()[0],
        "modacor": modacor.__version__,
        "numpy": np.__version__,
        "h5py": h5py.__version__,
        "settings": asdict(settings),
        "detector_shapes": shapes,
        "timing_definitions": {
            "server_process_s": "runtime pipeline execution only",
            "client_process_s": "blocking HTTP process request, including output publication",
            "source_bind_s": "sample-source registration immediately before a rerun",
            "workflow_s": "source_bind_s + client_process_s",
        },
    }


def _timed_process(session: Any, **kwargs: Any) -> tuple[dict[str, Any], dict[str, Any], float]:
    started = perf_counter()
    response = session.process(**kwargs)
    client_process_s = perf_counter() - started
    metadata = session.run(response["run_id"])
    return response, metadata, client_process_s


def _timing_row(
    *,
    workflow: str,
    detector: str,
    run_kind: str,
    measurement: str,
    frames: int,
    response: dict[str, Any],
    metadata: dict[str, Any],
    client_process_s: float,
    source_bind_s: float = 0.0,
    repeat: int | None = None,
    chunk_index: int | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    server_process_s = float(metadata["elapsed_s"])
    row = {
        "workflow": workflow,
        "detector": detector,
        "run_kind": run_kind,
        "measurement": measurement,
        "repeat": repeat,
        "chunk_index": chunk_index,
        "frames": frames,
        "effective_mode": response["effective_mode"],
        "server_process_s": server_process_s,
        "client_process_s": client_process_s,
        "source_bind_s": source_bind_s,
        "workflow_s": source_bind_s + client_process_s,
        "publication_overhead_s": max(0.0, client_process_s - server_process_s),
        "executed_steps": len(metadata.get("executed_steps", ())),
        "reused_steps": len(metadata.get("skipped_steps", ())),
        "frames_per_s": frames / (source_bind_s + client_process_s),
        "run_id": response["run_id"],
    }
    steps = [
        {
            "workflow": workflow,
            "detector": detector,
            "run_kind": run_kind,
            "measurement": measurement,
            "repeat": repeat,
            "chunk_index": chunk_index,
            "run_id": response["run_id"],
            "step_id": step_id,
            "seconds": seconds,
        }
        for step_id, seconds in metadata.get("step_durations_s", {}).items()
    ]
    return row, steps


def benchmark_server(
    client: Any,
    inputs: Any,
    run_dir: str | Path,
    settings: BenchmarkSettings,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Measure fresh-session full runs and new-sample partial reruns."""

    run_dir = Path(run_dir)
    measurements = inputs.measurements[: settings.measurement_limit]
    if len(measurements) < 2:
        raise ValueError("The server rerun benchmark needs at least two measurements.")
    rows: list[dict[str, Any]] = []
    step_rows: list[dict[str, Any]] = []

    for detector in settings.detectors:
        pipeline_yaml = validation_pipeline_yaml(inputs.pipeline_paths[detector])
        with h5py.File(measurements[0][1], "r") as source:
            server_frames = int(np.prod(source[DETECTOR_PATHS[detector]].shape[:2]))

        # Every full observation gets a fresh server session. Session creation and
        # source registration are setup and intentionally outside the timer.
        for repeat in range(settings.full_repeats):
            master, prepared = measurements[repeat % len(measurements)]
            session = client.replace_session(
                f"i22-perf-{detector.lower()}-full",
                name=f"I22 {detector} performance full run",
                pipeline_yaml=pipeline_yaml,
                trace={"enabled": False},
            )
            session.register_sources(*source_registrations(inputs, sample=prepared))
            process_kwargs: dict[str, Any] = {
                "mode": "full",
                "run_name": f"server_full_{detector.lower()}_{repeat:02d}",
                "rollback_snapshot": False,
            }
            if settings.write_server_hdf:
                output = run_dir / f"server_full_{detector.lower()}_{repeat:02d}.h5"
                process_kwargs["write_hdf"] = {
                    "path": str(output),
                    "data_paths": ["/sample/signal", "/sample/Q"],
                }
            response, metadata, client_s = _timed_process(session, **process_kwargs)
            row, steps = _timing_row(
                workflow="server",
                detector=detector,
                run_kind="initial/full",
                measurement=master.name,
                frames=server_frames,
                repeat=repeat,
                response=response,
                metadata=metadata,
                client_process_s=client_s,
            )
            rows.append(row)
            step_rows.extend(steps)
            print(f"{detector} full {repeat + 1}/{settings.full_repeats}: {row['workflow_s']:.2f} s")
            session.delete()

        # One unreported full run seeds the cached static/background state. Each
        # reported observation then swaps in a genuinely different sample source.
        seed_master, seed_prepared = measurements[0]
        session = client.replace_session(
            f"i22-perf-{detector.lower()}-partial",
            name=f"I22 {detector} performance partial reruns",
            pipeline_yaml=pipeline_yaml,
            trace={"enabled": False},
        )
        session.register_sources(*source_registrations(inputs, sample=seed_prepared))
        session.process(mode="full", run_name=f"partial_seed_{detector.lower()}", rollback_snapshot=False)
        rerun_measurements = measurements[1:]
        for repeat in range(settings.partial_repeats):
            master, prepared = rerun_measurements[repeat % len(rerun_measurements)]
            bind_started = perf_counter()
            session.register_source({"ref": "sample", "type": "hdf", "location": str(prepared)})
            bind_s = perf_counter() - bind_started
            process_kwargs = {
                "mode": "partial",
                "changed_sources": ["sample"],
                "run_name": f"server_partial_{detector.lower()}_{repeat:02d}",
                "rollback_snapshot": False,
            }
            if settings.write_server_hdf:
                output = run_dir / f"server_partial_{detector.lower()}_{repeat:02d}.h5"
                process_kwargs["write_hdf"] = {
                    "path": str(output),
                    "data_paths": ["/sample/signal", "/sample/Q"],
                }
            response, metadata, client_s = _timed_process(session, **process_kwargs)
            row, steps = _timing_row(
                workflow="server",
                detector=detector,
                run_kind="sample rerun",
                measurement=master.name,
                frames=server_frames,
                repeat=repeat,
                response=response,
                metadata=metadata,
                client_process_s=client_s,
                source_bind_s=bind_s,
            )
            rows.append(row)
            step_rows.extend(steps)
            print(f"{detector} partial {repeat + 1}/{settings.partial_repeats}: {row['workflow_s']:.2f} s")
        session.delete()

    return pd.DataFrame(rows), pd.DataFrame(step_rows)


def benchmark_chunked_hdf(
    client: Any,
    inputs: Any,
    run_dir: str | Path,
    settings: BenchmarkSettings,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Measure direct HDF slices through the persistent chunk-output workflow."""

    run_dir = Path(run_dir)
    measurements = inputs.measurements[: settings.measurement_limit]
    work_items = chunk_work_items(
        measurements,
        frame_count=settings.frame_count,
        chunk_size=settings.chunk_size,
    )
    output_path = run_dir / "i22_performance_chunks.h5"
    rows: list[dict[str, Any]] = []
    step_rows: list[dict[str, Any]] = []
    finalize_rows: list[dict[str, Any]] = []

    for detector in settings.detectors:
        source_shape = validate_chunk_sources(measurements, detector, frame_count=settings.frame_count)
        session = client.replace_session(
            f"i22-perf-{detector.lower()}-chunks",
            name=f"I22 {detector} direct HDF chunk performance",
            pipeline_yaml=validation_pipeline_yaml(inputs.pipeline_paths[detector]),
            trace={"enabled": False},
        )
        session.register_sources(
            *source_registrations(inputs),
            {"ref": "sample", "type": "buffer", "location": "buffer://session"},
        )

        # The pilot establishes the output schema and warms the pipeline. It is
        # deliberately excluded from the benchmark observations.
        first = work_items[0]
        upload_sample_chunk(session.source_buffer("sample"), detector, first.source_path, first.start, first.stop)
        pilot_name = f"chunk_pilot_{detector.lower()}"
        pilot_path = run_dir / f"{pilot_name}.h5"
        session.process(
            mode="full",
            run_name=pilot_name,
            rollback_snapshot=False,
            write_hdf={"path": str(pilot_path), "data_paths": ["/sample/signal"]},
        )
        plan = build_complete_plan(
            pilot_path,
            pilot_name,
            detector,
            measurements,
            source_shape,
            frame_count=settings.frame_count,
            chunk_size=settings.chunk_size,
            source_mode="hdf",
        )
        output = client.chunked_outputs.create(
            sink={
                "ref": f"i22_{detector.lower()}_performance_chunks",
                "type": "hdf_chunked",
                "location": str(output_path),
                "kwargs": {
                    "compression": settings.compression,
                    "compression_opts": settings.compression_level,
                },
            },
            subpath=f"processed_{detector.lower()}",
            plan=plan,
            collision="error",
        )

        current_source: Path | None = None
        for ordinal, item in enumerate(work_items):
            bind_s = 0.0
            if item.source_path != current_source:
                bind_started = perf_counter()
                session.register_source(direct_sample_registration("hdf", item.source_path))
                bind_s = perf_counter() - bind_started
                current_source = item.source_path
            response, metadata, client_s = _timed_process(
                session,
                mode="partial",
                changed_sources=["sample"],
                run_name=(
                    f"chunk_{detector.lower()}_m{item.measurement_index:03d}_c{item.chunk_index:03d}"
                ),
                rollback_snapshot=False,
                chunk_output=output.chunk(chunk_spec(plan, item)),
            )
            row, steps = _timing_row(
                workflow="chunked HDF",
                detector=detector,
                run_kind=f"{settings.chunk_size}-frame chunk",
                measurement=item.master_path.name,
                frames=item.stop - item.start,
                chunk_index=item.chunk_index,
                response=response,
                metadata=metadata,
                client_process_s=client_s,
                source_bind_s=bind_s,
            )
            rows.append(row)
            step_rows.extend(steps)
            if ordinal == 0 or (ordinal + 1) % 10 == 0 or ordinal + 1 == len(work_items):
                print(f"{detector} chunk {ordinal + 1}/{len(work_items)}: {row['workflow_s']:.2f} s")

        finalize_started = perf_counter()
        final = output.finalize()
        finalize_s = perf_counter() - finalize_started
        finalize_rows.append(
            {
                "detector": detector,
                "chunks": final["completed_chunks"],
                "finalize_s": finalize_s,
                "output": str(output_path),
            }
        )
        session.delete()

    return pd.DataFrame(rows), pd.DataFrame(step_rows), pd.DataFrame(finalize_rows)


def summarize_timings(timings: pd.DataFrame) -> pd.DataFrame:
    """Return median and 10th--90th percentile intervals by benchmark class."""

    grouped = timings.groupby(["workflow", "detector", "run_kind"], sort=False)
    summary = grouped.agg(
        observations=("workflow_s", "size"),
        frames=("frames", "median"),
        median_workflow_s=("workflow_s", "median"),
        p10_workflow_s=("workflow_s", lambda values: values.quantile(0.10)),
        p90_workflow_s=("workflow_s", lambda values: values.quantile(0.90)),
        median_server_process_s=("server_process_s", "median"),
        median_publication_overhead_s=("publication_overhead_s", "median"),
        median_frames_per_s=("frames_per_s", "median"),
        executed_steps=("executed_steps", "median"),
        reused_steps=("reused_steps", "median"),
    )
    return summary.reset_index()


def save_results(
    run_dir: str | Path,
    settings: BenchmarkSettings,
    inputs: Any,
    timings: pd.DataFrame,
    step_timings: pd.DataFrame,
    finalize_timings: pd.DataFrame,
) -> pd.DataFrame:
    run_dir = Path(run_dir)
    summary = summarize_timings(timings)
    timings.to_csv(run_dir / "i22_performance_raw.csv", index=False)
    step_timings.to_csv(run_dir / "i22_performance_steps.csv", index=False)
    finalize_timings.to_csv(run_dir / "i22_performance_finalize.csv", index=False)
    summary.to_csv(run_dir / "i22_performance_summary.csv", index=False)
    (run_dir / "i22_performance_metadata.json").write_text(
        json.dumps(system_metadata(settings, inputs), indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def plot_overview(summary: pd.DataFrame, run_dir: str | Path) -> plt.Figure:
    """Make a compact, poster-ready latency/throughput summary."""

    run_dir = Path(run_dir)
    colors = plt.colormaps["plasma"](np.linspace(0.25, 0.78, 2))
    detectors = [detector for detector in ("SAXS", "WAXS") if detector in set(summary["detector"])]
    fig, (latency_ax, throughput_ax) = plt.subplots(1, 2, figsize=(8.0, 3.35), layout="constrained")

    server = summary[summary["workflow"] == "server"]
    kinds = [kind for kind in ("initial/full", "sample rerun") if kind in set(server["run_kind"])]
    x = np.arange(len(kinds), dtype=float)
    width = 0.34
    for index, detector in enumerate(detectors):
        values = server.set_index(["detector", "run_kind"]).loc[detector]
        medians = np.array([values.loc[kind, "median_workflow_s"] for kind in kinds])
        lower = medians - np.array([values.loc[kind, "p10_workflow_s"] for kind in kinds])
        upper = np.array([values.loc[kind, "p90_workflow_s"] for kind in kinds]) - medians
        positions = x + (index - (len(detectors) - 1) / 2) * width
        bars = latency_ax.bar(
            positions,
            medians,
            width=width,
            color=colors[index],
            label=detector,
            yerr=np.vstack([lower, upper]),
            capsize=3,
        )
        latency_ax.bar_label(bars, fmt="%.1f s", padding=3, fontsize=8)
    latency_ax.set_xticks(x, ["Initial/full", "New sample\npartial rerun"])
    latency_ax.set_ylabel("End-to-end time (s)")
    latency_ax.set_title("100-frame I(q)")
    latency_ax.spines[["top", "right"]].set_visible(False)
    latency_ax.legend(frameon=False)

    chunks = summary[summary["workflow"] == "chunked HDF"].set_index("detector")
    bars = throughput_ax.bar(
        detectors,
        [chunks.loc[detector, "median_frames_per_s"] for detector in detectors],
        color=colors[: len(detectors)],
        width=0.58,
    )
    throughput_ax.bar_label(bars, fmt="%.1f", padding=3, fontsize=9)
    throughput_ax.set_ylabel("Throughput (detector frames s$^{-1}$)")
    throughput_ax.set_title("Direct-slice chunked HDF")
    throughput_ax.spines[["top", "right"]].set_visible(False)

    for suffix in ("png", "svg", "pdf"):
        fig.savefig(run_dir / f"i22_performance_overview.{suffix}", dpi=300, bbox_inches="tight")
    return fig


def combine_frames(frames: Iterable[pd.DataFrame]) -> pd.DataFrame:
    """Concatenate non-empty timing frames while retaining stable columns."""

    present = [frame for frame in frames if not frame.empty]
    return pd.concat(present, ignore_index=True) if present else pd.DataFrame()
