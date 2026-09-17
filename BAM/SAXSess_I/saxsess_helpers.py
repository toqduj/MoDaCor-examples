"""Preprocessing helpers for the BAM SAXSess-I N008 reference example."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import h5py
import numpy as np
import pandas as pd
import yaml
from PIL import Image, ImageSequence

from modacor import Q_
from modacor.dataclasses.basedata import BaseData


UNCERTAINTY_KEY = "propagate_to_all"
MEASUREMENTS_HEADER_ROW = 3
SAMPLES_HEADER_ROW = 2


def normalize_measurement_id(value: object) -> str:
    """Return a stripped SAXSess measurement stem, or an empty string."""

    if value is None or pd.isna(value):
        return ""
    text = str(value).strip()
    return "" if not text or text.lower() == "nan" else Path(text).stem


def load_metadata(workbook: str | Path) -> pd.DataFrame:
    """Read and validate the fixed-layout SAXSess workbook."""

    workbook = Path(workbook)
    measurement_columns = [
        "measurement_id",
        "sample_name",
        "background_id",
        "transmission_id",
        "i_cal_reference",
        "thickness_m",
        "thickness_sigma_m",
    ]
    sample_columns = ["sample_name", "sample_physical_state"]

    measurements = pd.read_excel(
        workbook,
        sheet_name="Measurements",
        header=MEASUREMENTS_HEADER_ROW,
        engine="openpyxl",
    )
    samples = pd.read_excel(
        workbook,
        sheet_name="Samples",
        header=SAMPLES_HEADER_ROW,
        engine="openpyxl",
    )
    measurements.columns = [str(column).strip() for column in measurements.columns]
    samples.columns = [str(column).strip() for column in samples.columns]

    for sheet, frame, required in (
        ("Measurements", measurements, measurement_columns),
        ("Samples", samples, sample_columns),
    ):
        missing = sorted(set(required).difference(frame.columns))
        if missing:
            raise ValueError(
                f"{sheet} is missing required columns: {', '.join(missing)}"
            )

    measurements = (
        measurements.dropna(how="all").dropna(subset=["measurement_id"]).copy()
    )
    samples = samples.dropna(how="all").dropna(subset=["sample_name"]).copy()
    for column in (
        "measurement_id",
        "background_id",
        "transmission_id",
        "i_cal_reference",
    ):
        measurements[column] = measurements[column].map(normalize_measurement_id)
    measurements["sample_name"] = (
        measurements["sample_name"].fillna("").astype(str).str.strip()
    )
    samples["sample_name"] = samples["sample_name"].fillna("").astype(str).str.strip()

    metadata = measurements.merge(
        samples, on="sample_name", how="left", suffixes=("", "_sample")
    )
    metadata = metadata.reset_index(drop=True)
    duplicates = sorted(
        metadata.loc[metadata["measurement_id"].duplicated(), "measurement_id"].unique()
    )
    if duplicates:
        raise ValueError(f"Duplicate measurement ids: {', '.join(duplicates)}")
    return metadata


def build_n008_plan(metadata: pd.DataFrame) -> pd.DataFrame:
    """Select the N008 aliquots and resolve their water-calibration branches."""

    indexed = metadata.set_index("measurement_id", drop=False)
    selected = metadata[
        metadata["sample_name"].str.contains("N008", case=False, na=False)
        & metadata["sample_physical_state"].str.lower().eq("liquid")
    ].copy()
    if selected.empty:
        raise ValueError("No liquid N008 measurements were found in the workbook.")

    rows: list[dict[str, str]] = []
    for row in selected.itertuples(index=False):
        sample_id = row.measurement_id
        calibration_id = row.background_id
        if calibration_id not in indexed.index:
            raise ValueError(
                f"{sample_id}: missing background/intensity calibration {calibration_id!r}"
            )
        calibration_background_id = indexed.loc[calibration_id, "background_id"]
        if calibration_background_id not in indexed.index:
            raise ValueError(
                f"{sample_id}: missing intensity-calibration background {calibration_background_id!r}"
            )
        declared = row.i_cal_reference
        if declared and declared != calibration_background_id:
            raise ValueError(
                f"{sample_id}: i_cal_reference={declared!r}, expected {calibration_background_id!r} "
                "from the water background chain"
            )
        rows.append(
            {
                "measurement_id": sample_id,
                "sample_name": row.sample_name,
                "sample_background_id": calibration_id,
                "intensity_calibration_id": calibration_id,
                "intensity_calibration_background_id": calibration_background_id,
            }
        )
    return pd.DataFrame(rows).sort_values("measurement_id").reset_index(drop=True)


def required_tiff_ids(metadata: pd.DataFrame) -> set[str]:
    """Return measurement and foil-transmission ids required by the workbook."""

    return {
        measurement_id
        for column in ("measurement_id", "transmission_id")
        for measurement_id in metadata[column]
        if measurement_id
    }


def validate_source_files(metadata: pd.DataFrame, raw_dir: str | Path) -> None:
    """Require exactly the TIFF dependency closure named by the workbook."""

    raw_dir = Path(raw_dir)
    expected = required_tiff_ids(metadata)
    available = {path.stem for path in raw_dir.glob("*.tif")}
    missing = sorted(expected.difference(available))
    unexpected = sorted(available.difference(expected))
    if missing:
        raise FileNotFoundError(f"Missing TIFF inputs: {', '.join(missing)}")
    if unexpected:
        raise ValueError(
            f"Unreferenced TIFF inputs should not be packaged: {', '.join(unexpected)}"
        )


def load_tiff_stack(path: str | Path) -> np.ndarray:
    """Load a multi-frame SAXSess TIFF as ``(frame, slow, fast)`` float32 data."""

    path = Path(path)
    with Image.open(path) as image:
        frames = [np.asarray(frame.copy()) for frame in ImageSequence.Iterator(image)]
    if not frames:
        raise ValueError(f"TIFF contains no frames: {path}")
    stack = np.stack(frames).astype(np.float32, copy=False)
    if stack.ndim != 3:
        raise ValueError(
            f"Expected a three-dimensional TIFF stack, got {stack.shape} in {path}"
        )
    return stack


def _base_data(config: Mapping[str, Any], *keys: str) -> BaseData:
    node: Any = config
    for key in keys:
        node = node[key]
    return BaseData(
        signal=node["value"],
        units=node["units"],
        uncertainties={UNCERTAINTY_KEY: node["uncertainty"]},
    )


def _fluorescence_integral(stack: np.ndarray, pixel_range: tuple[int, int]) -> float:
    profile = np.nansum(stack.squeeze(), axis=0)
    return float(np.nansum(profile[slice(*pixel_range)]))


def _write_quantity(
    group: h5py.Group,
    name: str,
    quantity: Any,
    uncertainty: Any | None = None,
    attrs: Mapping[str, Any] | None = None,
) -> None:
    options = (
        {"compression": "gzip", "compression_opts": 4}
        if np.ndim(quantity.magnitude)
        else {}
    )
    dataset = group.create_dataset(name, data=quantity.magnitude, **options)
    dataset.attrs["units"] = str(quantity.units)
    if uncertainty is not None:
        uncertainty_options = (
            {"compression": "gzip", "compression_opts": 4}
            if np.ndim(uncertainty.magnitude)
            else {}
        )
        uncertainty_dataset = group.create_dataset(
            f"{name}_uncertainty",
            data=uncertainty.magnitude,
            **uncertainty_options,
        )
        uncertainty_dataset.attrs["units"] = str(uncertainty.units)
        dataset.attrs["uncertainties"] = f"{name}_uncertainty"
    for key, value in (attrs or {}).items():
        dataset.attrs[key] = value


def _write_base_data(
    group: h5py.Group,
    name: str,
    value: BaseData,
    attrs: Mapping[str, Any] | None = None,
) -> None:
    options = (
        {"compression": "gzip", "compression_opts": 4} if np.ndim(value.signal) else {}
    )
    dataset = group.create_dataset(name, data=value.signal, **options)
    dataset.attrs["units"] = str(value.units)
    if UNCERTAINTY_KEY in value.uncertainties:
        uncertainty = value.uncertainties[UNCERTAINTY_KEY]
        uncertainty_options = (
            {"compression": "gzip", "compression_opts": 4}
            if np.ndim(uncertainty)
            else {}
        )
        uncertainty_dataset = group.create_dataset(
            f"{name}_uncertainty",
            data=uncertainty,
            **uncertainty_options,
        )
        uncertainty_dataset.attrs["units"] = str(value.units)
        dataset.attrs["uncertainties"] = f"{name}_uncertainty"
    for key, attribute_value in (attrs or {}).items():
        dataset.attrs[key] = attribute_value


def _metadata_text(row: pd.Series, key: str) -> str:
    value = row.get(key, "")
    return "" if value is None or pd.isna(value) else str(value)


def preprocess_measurements(
    metadata: pd.DataFrame,
    *,
    raw_dir: str | Path,
    static_config: str | Path,
    output_dir: str | Path,
) -> dict[str, Path]:
    """Convert the TIFF acquisition set into MoDaCor-readable HDF5 sources."""

    raw_dir = Path(raw_dir)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    validate_source_files(metadata, raw_dir)
    config = yaml.safe_load(Path(static_config).read_text(encoding="utf-8"))

    wavelength = _base_data(config, "wavelength")
    detector_distance = _base_data(config, "geometry", "sample_to_detector_distance")
    pixel_height = _base_data(config, "detector", "pixel_height")
    pixel_width = _base_data(config, "detector", "pixel_width")
    exposure_time = _base_data(config, "data_settings", "frame_exposure_time")
    search_center = int(config["data_settings"]["approx_beam_center_pixel"])
    search_width = int(config["data_settings"]["peak_max_search_width"])
    fluorescence_range = tuple(
        int(value) for value in config["data_settings"]["fluorescence_pixel_range"]
    )

    indexed = metadata.set_index("measurement_id", drop=False)
    stack_cache: dict[str, np.ndarray] = {}
    integral_cache: dict[str, float] = {}
    transmission_cache: dict[str, BaseData] = {}

    def stack(measurement_id: str) -> np.ndarray:
        if measurement_id not in stack_cache:
            stack_cache[measurement_id] = load_tiff_stack(
                raw_dir / f"{measurement_id}.tif"
            )
        return stack_cache[measurement_id]

    def integral(measurement_id: str) -> float:
        if measurement_id not in integral_cache:
            integral_cache[measurement_id] = _fluorescence_integral(
                stack(measurement_id), fluorescence_range
            )
        return integral_cache[measurement_id]

    def local_transmission(measurement_id: str) -> BaseData:
        row = indexed.loc[measurement_id]
        parent_id = row["background_id"]
        if not parent_id:
            return BaseData(
                signal=1.0,
                uncertainties={UNCERTAINTY_KEY: 0.0},
                units="dimensionless",
            )
        child_counts = integral(row["transmission_id"])
        parent_counts = integral(indexed.loc[parent_id, "transmission_id"])
        value = child_counts / parent_counts
        uncertainty = value * np.sqrt(1.0 / child_counts + 1.0 / parent_counts)
        return BaseData(
            signal=value,
            uncertainties={UNCERTAINTY_KEY: uncertainty},
            units="dimensionless",
        )

    def overall_transmission(
        measurement_id: str, active: set[str] | None = None
    ) -> BaseData:
        if measurement_id in transmission_cache:
            return transmission_cache[measurement_id]
        active = set() if active is None else active
        if measurement_id in active:
            raise ValueError(f"Cycle in background chain at {measurement_id}")
        active.add(measurement_id)
        row = indexed.loc[measurement_id]
        value = local_transmission(measurement_id)
        if row["background_id"]:
            value = value * overall_transmission(row["background_id"], active)
        active.remove(measurement_id)
        transmission_cache[measurement_id] = value
        return value

    def root_measurement(measurement_id: str) -> str:
        seen: set[str] = set()
        while indexed.loc[measurement_id, "background_id"]:
            if measurement_id in seen:
                raise ValueError(f"Cycle in background chain at {measurement_id}")
            seen.add(measurement_id)
            measurement_id = indexed.loc[measurement_id, "background_id"]
        return measurement_id

    outputs: dict[str, Path] = {}
    for _, row in metadata.iterrows():
        measurement_id = row["measurement_id"]
        detector_data = stack(measurement_id)
        mean_profile = np.nanmean(detector_data.squeeze(), axis=0)
        lower = max(0, search_center - search_width)
        upper = min(len(mean_profile), search_center + search_width + 1)
        beam_center = int(np.nanargmax(mean_profile[lower:upper])) + lower
        beam_center_y = BaseData(
            signal=beam_center,
            uncertainties={UNCERTAINTY_KEY: 1.0},
            units="",
        )

        root_id = root_measurement(measurement_id)
        root_transmission_id = indexed.loc[root_id, "transmission_id"]
        root_counts = integral(root_transmission_id)
        relative_flux = (
            BaseData(
                signal=root_counts,
                uncertainties={UNCERTAINTY_KEY: np.sqrt(root_counts)},
                units="AFU",
            )
            / exposure_time
        )

        thickness_value = row.get("thickness_m", np.nan)
        thickness_uncertainty = row.get("thickness_sigma_m", np.nan)
        thickness = BaseData(
            signal=float(thickness_value) if pd.notna(thickness_value) else np.nan,
            uncertainties={
                UNCERTAINTY_KEY: (
                    abs(float(thickness_uncertainty))
                    if pd.notna(thickness_uncertainty)
                    else np.nan
                )
            },
            units="m",
        )

        output = (output_dir / f"{measurement_id}.h5").resolve()
        with h5py.File(output, "w") as h5_file:
            entry = h5_file.create_group("entry")
            entry.attrs.update(
                {
                    "NX_class": "NXentry",
                    "default": "instrument",
                    "creator": "MoDaCor SAXSess-I example preprocessor",
                    "measurement_id": measurement_id,
                    "source_tiff": f"{measurement_id}.tif",
                    **{
                        key: _metadata_text(row, key)
                        for key in (
                            "sample_name",
                            "project_name",
                            "background_id",
                            "transmission_id",
                            "sample_holder",
                            "cell_temperature",
                            "measurement_date",
                            "partner_name",
                            "sample_physical_state",
                            "sample_manufacturer",
                            "sample_supplier",
                            "sample_bam_oe",
                            "sample_bam_location",
                            "sample_description",
                        )
                    },
                }
            )

            instrument = entry.create_group("instrument")
            instrument.attrs.update(
                {
                    "NX_class": "NXinstrument",
                    "default": "detector",
                    **{key: value for key, value in config["instrument"].items()},
                }
            )
            _write_base_data(instrument, "sample_detector_distance", detector_distance)

            detector = instrument.create_group("detector")
            detector.attrs.update(
                {
                    "NX_class": "NXdetector",
                    "default": "data",
                    **{
                        key: config["detector"][key]
                        for key in ("name", "type", "manufacturer")
                    },
                }
            )
            _write_base_data(detector, "y_pixel_size", pixel_height)
            _write_base_data(detector, "x_pixel_size", pixel_width)
            _write_quantity(
                detector,
                "data",
                Q_(detector_data, "counts"),
                attrs={"description": "Raw Mythen2 counts by frame and strip"},
            )
            _write_base_data(detector, "frame_exposure_time", exposure_time)
            _write_quantity(detector, "beam_center_x", Q_(0.5, ""), Q_(0.0, ""))
            _write_base_data(detector, "beam_center_y", beam_center_y)
            _write_quantity(
                detector,
                "beam_center",
                Q_(np.array([beam_center, 0.5]), ""),
                Q_(np.array([1.0, 0.001]), ""),
            )

            transformations = detector.create_group("transformations")
            transformations.attrs["NX_class"] = "NXtransformations"
            _write_quantity(
                transformations,
                "det_x",
                Q_(-0.5 * pixel_width.signal, str(pixel_width.units)),
                Q_(pixel_width.uncertainties[UNCERTAINTY_KEY], str(pixel_width.units)),
                attrs={
                    "vector": [1.0, 0.0, 0.0],
                    "offset": [0.0, 0.0, 0.0],
                    "depends_on": ".",
                },
            )
            _write_base_data(
                transformations,
                "det_y",
                -beam_center_y * pixel_height,
                attrs={
                    "vector": [0.0, 1.0, 0.0],
                    "offset": [0.0, 0.0, 0.0],
                    "depends_on": ".",
                },
            )
            _write_base_data(
                transformations,
                "det_z",
                detector_distance,
                attrs={
                    "vector": [0.0, 0.0, 1.0],
                    "offset": [0.0, 0.0, 0.0],
                    "depends_on": ".",
                },
            )

            sample = entry.create_group("sample")
            sample.attrs.update(
                {
                    "NX_class": "NXsample",
                    "name": _metadata_text(row, "sample_name"),
                    "description": "Sample metadata imported from metadata_N008.xlsx",
                }
            )
            _write_base_data(
                sample,
                "transmission",
                overall_transmission(measurement_id),
                attrs={
                    "description": "Product of foil ratios along the background chain"
                },
            )
            _write_base_data(
                sample,
                "transmission_local",
                local_transmission(measurement_id),
                attrs={"description": "Foil ratio relative to the direct background"},
            )
            beam = sample.create_group("beam")
            beam.attrs["NX_class"] = "NXbeam"
            _write_base_data(
                beam,
                "relative_flux",
                relative_flux,
                attrs={
                    "description": f"Foil fluorescence of root measurement {root_id}"
                },
            )
            _write_base_data(beam, "incident_wavelength", wavelength)
            thickness_group = sample.create_group("thickness_averaged")
            _write_base_data(thickness_group, "mean", thickness)
            sample_transformations = sample.create_group("transformations")
            sample_transformations.attrs["NX_class"] = "NXtransformations"
            _write_quantity(
                sample_transformations,
                "sample_z",
                Q_(0.0, "mm"),
                Q_(0.0, "mm"),
                attrs={
                    "vector": [0.0, 0.0, 1.0],
                    "offset": [0.0, 0.0, 0.0],
                    "depends_on": ".",
                },
            )

        outputs[measurement_id] = output
    return outputs


def read_absolute_csv(path: str | Path) -> pd.DataFrame:
    """Read a pipeline CSV result while retaining concise column names."""

    result = pd.read_csv(path, sep=";", header=0, skiprows=[1])
    return result.rename(
        columns={
            "sample/Q/signal": "Q",
            "sample/Q/uncertainties/uncertainty_combined": "Q_sigma",
            "sample/signal/signal": "I",
            "sample/signal/uncertainties/uncertainty_total": "I_sigma",
        }
    )
