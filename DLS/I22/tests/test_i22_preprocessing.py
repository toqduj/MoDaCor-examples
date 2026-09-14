from __future__ import annotations

import sys
from pathlib import Path

import h5py
import numpy as np
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from i22_helpers import preprocess_measurement, sample_aligned_paths


def _write_measurement(
    path: Path,
    *,
    bsdiodes: np.ndarray,
    i0: np.ndarray,
) -> None:
    leading_shape = bsdiodes.shape[:2]
    with h5py.File(path, "w") as h5:
        entry = h5.create_group("entry1")
        entry.attrs["NX_class"] = "NXentry"
        entry.create_dataset("detector/data", data=np.zeros((*leading_shape, 2, 3)))
        entry.create_dataset("Pilatus2M_WAXS/data", data=np.zeros((*leading_shape, 3, 2)))
        entry.create_dataset("bsdiodes/data", data=bsdiodes)
        entry.create_dataset("I0/data", data=i0)
        entry.create_dataset("instrument/detector/count_time", data=np.ones(leading_shape))
        entry.create_dataset("instrument/Pilatus2M_WAXS/count_time", data=np.ones(leading_shape))
        sample = entry.create_group("sample")
        sample.attrs["NX_class"] = "NXsample"
        sample.create_dataset("thickness", data=0.5)


def _readout(values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    channel = values[..., 1]
    return np.mean(channel, axis=-1), np.std(channel, axis=-1, ddof=1) / np.sqrt(channel.shape[-1])


def test_preprocessing_calibrates_transmission_and_propagates_sem(tmp_path: Path) -> None:
    measurement_bsdiodes = np.zeros((1, 2, 4, 2))
    measurement_i0 = np.zeros((1, 2, 4, 2))
    measurement_bsdiodes[..., 1] = [[(3.0, 4.0, 5.0, 4.0), (7.0, 8.0, 9.0, 8.0)]]
    measurement_i0[..., 1] = [[(1.0, 2.0, 3.0, 2.0), (1.0, 2.0, 3.0, 2.0)]]

    # Deliberately use a different leading shape to confirm that the reference
    # supplies one portable scalar calibration rather than frame-paired values.
    reference_bsdiodes = np.zeros((1, 3, 2, 2))
    reference_i0 = np.zeros((1, 3, 2, 2))
    reference_bsdiodes[..., 1] = [[[5.0, 7.0], [6.0, 6.0], [7.0, 5.0]]]
    reference_i0[..., 1] = [[[1.0, 3.0], [2.0, 2.0], [3.0, 1.0]]]

    measurement = tmp_path / "measurement.nxs"
    reference = tmp_path / "open_beam.nxs"
    _write_measurement(measurement, bsdiodes=measurement_bsdiodes, i0=measurement_i0)
    _write_measurement(reference, bsdiodes=reference_bsdiodes, i0=reference_i0)

    output = preprocess_measurement(
        measurement,
        tmp_path / "preprocessed",
        transmission_reference_file=reference,
    )

    diode_mean, diode_sem = _readout(measurement_bsdiodes)
    i0_mean, i0_sem = _readout(measurement_i0)
    reference_diode = reference_bsdiodes[..., 1].reshape(-1)
    reference_i0_values = reference_i0[..., 1].reshape(-1)
    reference_diode_mean = np.mean(reference_diode)
    reference_i0_mean = np.mean(reference_i0_values)
    reference_diode_sem = np.std(reference_diode, ddof=1) / np.sqrt(reference_diode.size)
    reference_i0_sem = np.std(reference_i0_values, ddof=1) / np.sqrt(reference_i0_values.size)
    reference_ratio = reference_diode_mean / reference_i0_mean
    reference_ratio_sem = reference_ratio * np.hypot(
        reference_diode_sem / reference_diode_mean,
        reference_i0_sem / reference_i0_mean,
    )
    expected = (diode_mean / i0_mean) / reference_ratio
    expected_sem = expected * np.sqrt(
        (diode_sem / diode_mean) ** 2
        + (i0_sem / i0_mean) ** 2
        + (reference_ratio_sem / reference_ratio) ** 2
    )

    with h5py.File(output, "r") as h5:
        np.testing.assert_allclose(h5["/entry1/sample/transmission"][..., 0, 0], expected)
        np.testing.assert_allclose(h5["/entry1/sample/transmission_sem"][..., 0, 0], expected_sem)
        np.testing.assert_allclose(h5["/modacor/normalization/i0_channel_1_mean"][..., 0, 0], i0_mean)
        np.testing.assert_allclose(h5["/modacor/calibration/bsdiodes_to_i0_ratio"][()], reference_ratio)
        np.testing.assert_allclose(h5["/modacor/calibration/bsdiodes_to_i0_ratio_sem"][()], reference_ratio_sem)
        assert h5["/entry1/sample/transmission"].attrs["units"] == "dimensionless"
        assert h5["/entry1/sample/transmission_sem"].attrs["units"] == "dimensionless"
        assert isinstance(h5.get("/entry1/sample/thickness", getlink=True), h5py.ExternalLink)

    with h5py.File(measurement, "r") as source:
        assert "/entry1/sample/transmission" not in source


def test_reference_file_changes_invalidate_cached_preprocessing(tmp_path: Path) -> None:
    values = np.zeros((1, 1, 3, 2))
    values[..., 1] = 2.0
    measurement = tmp_path / "measurement.nxs"
    reference_one = tmp_path / "open_beam_one.nxs"
    reference_two = tmp_path / "open_beam_two.nxs"
    _write_measurement(measurement, bsdiodes=values * 2.0, i0=values)
    _write_measurement(reference_one, bsdiodes=values * 2.0, i0=values)
    _write_measurement(reference_two, bsdiodes=values * 4.0, i0=values)

    output = preprocess_measurement(
        measurement,
        tmp_path / "preprocessed",
        transmission_reference_file=reference_one,
    )
    with h5py.File(output, "r") as h5:
        np.testing.assert_allclose(h5["/entry1/sample/transmission"][()], 1.0)

    preprocess_measurement(
        measurement,
        tmp_path / "preprocessed",
        transmission_reference_file=reference_two,
    )
    with h5py.File(output, "r") as h5:
        np.testing.assert_allclose(h5["/entry1/sample/transmission"][()], 0.5)
        assert h5["/modacor/calibration"].attrs["transmission_reference_file"].endswith(
            "open_beam_two.nxs"
        )


def test_operational_pipelines_normalize_time_before_transmission_and_flux() -> None:
    project_dir = Path(__file__).resolve().parents[1]
    for detector in ("SAXS", "WAXS"):
        pipeline = yaml.safe_load(
            (project_dir / "pipelines" / f"I22_{detector}_solids_operando.yaml").read_text()
        )
        steps = pipeline["steps"]
        assert not any(step_id.startswith("BS_") for step_id in steps)

        for processing_key in ("sample", "background"):
            suffix = f"_{processing_key}"
            assert steps[f"TI{suffix}"]["requires_steps"] == [f"PU{suffix}"]
            assert steps[f"TR{suffix}"]["requires_steps"] == [f"TI{suffix}"]
            assert steps[f"FL{suffix}"]["requires_steps"] == [f"TR{suffix}"]
            assert steps[f"FA{suffix}"]["requires_steps"] == [f"FL{suffix}"]

            source_ref = "sample" if processing_key == "sample" else "background"
            transmission = steps[f"TR{suffix}"]["configuration"]
            flux = steps[f"FL{suffix}"]["configuration"]
            assert transmission["divisor_source"] == f"{source_ref}::/entry1/sample/transmission"
            assert transmission["divisor_uncertainties_sources"] == {
                "transmission_SEM": f"{source_ref}::/entry1/sample/transmission_sem"
            }
            assert flux["divisor_source"] == (
                f"{source_ref}::/modacor/normalization/i0_channel_1_mean"
            )
            assert flux["divisor_uncertainties_sources"] == {
                "I0_SEM": f"{source_ref}::/modacor/normalization/i0_channel_1_sem"
            }

        aligned = sample_aligned_paths(detector)
        assert "/modacor/normalization/i0_channel_1_mean" in aligned
        assert "/modacor/normalization/i0_channel_1_sem" in aligned
        assert "/modacor/normalization/bsdiodes_channel_1_mean" not in aligned
