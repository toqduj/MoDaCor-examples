from __future__ import annotations

from pathlib import Path

import h5py
import numpy as np
import pytest

from i22_helpers import _pilot_output_layout, chunk_work_items, preprocess_measurement


def _write_measurement(path: Path) -> None:
    with h5py.File(path, "w") as h5:
        h5.create_dataset("/entry1/detector/data", data=np.ones((1, 2, 2, 3)))
        h5.create_dataset("/entry1/Pilatus2M_WAXS/data", data=np.ones((1, 2, 3, 2)))
        diode = np.arange(12, dtype=float).reshape(1, 2, 3, 2)
        h5.create_dataset("/entry1/bsdiodes/data", data=diode)
        for detector in ("detector", "Pilatus2M_WAXS"):
            count_time = h5.create_dataset(f"/entry1/instrument/{detector}/count_time", data=1.0)
            count_time.attrs["units"] = "s"
        h5.create_dataset("/entry1/I0/transmission", data=np.ones((1, 2)))


def _write_pilot(path: Path, *, weights_shape=(4,), uncertainty_shape=(4,)) -> None:
    with h5py.File(path, "w") as h5:
        group = h5.require_group("/processing/result/pilot/sample/signal")
        signal = group.create_dataset("signal", data=np.ones(4))
        signal.attrs["units"] = "1/cm"
        signal.attrs["rank_of_data"] = 1
        group.create_dataset("weights", data=np.ones(weights_shape))
        group.require_group("uncertainties").create_dataset("poisson", data=np.ones(uncertainty_shape))


def test_preprocessing_cache_depends_on_absolute_intensity_factor(tmp_path):
    source = tmp_path / "measurement.nxs"
    output_dir = tmp_path / "prepared"
    _write_measurement(source)

    output = preprocess_measurement(source, output_dir, absolute_intensity_factor=1.0)
    with h5py.File(output, "r") as h5:
        first_signature = h5.attrs["preprocessing_signature"]
        assert h5["/modacor/calibration/absolute_intensity_factor"][()] == 1.0

    preprocess_measurement(source, output_dir, absolute_intensity_factor=2.0)
    with h5py.File(output, "r") as h5:
        assert h5.attrs["preprocessing_signature"] != first_signature
        assert h5["/modacor/calibration/absolute_intensity_factor"][()] == 2.0
        assert h5["/modacor/normalization"].attrs["bsdiodes_channel_index"] == 1


@pytest.mark.parametrize(
    ("frame_count", "chunk_size", "match"),
    [(0, 1, "frame_count"), (1, 0, "chunk_size"), (True, 1, "frame_count")],
)
def test_chunk_work_items_rejects_invalid_grid(frame_count, chunk_size, match):
    measurements = ((Path("master.nxs"), Path("prepared.nxs")),)

    with pytest.raises(ValueError, match=match):
        chunk_work_items(measurements, frame_count=frame_count, chunk_size=chunk_size)


def test_chunk_work_items_requires_measurements():
    with pytest.raises(ValueError, match="measurements"):
        chunk_work_items((), frame_count=1, chunk_size=1)


@pytest.mark.parametrize(
    ("weights_shape", "uncertainty_shape", "match"),
    [((3,), (4,), "weights shape"), ((4,), (3,), "uncertainty.*shape")],
)
def test_pilot_layout_rejects_mismatched_per_chunk_arrays(
    tmp_path, weights_shape, uncertainty_shape, match
):
    pilot = tmp_path / "pilot.h5"
    _write_pilot(pilot, weights_shape=weights_shape, uncertainty_shape=uncertainty_shape)

    with pytest.raises(ValueError, match=match):
        _pilot_output_layout(pilot, "pilot", measurement_count=2, chunks_per_measurement=3)
