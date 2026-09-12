# Diamond Light Source I22 example

This example demonstrates batch processing of operando SAXS and WAXS data from
Diamond Light Source beamline I22. Four sample measurements are paired with one
empty-cell background and detector-specific calibration and mask files.

## Packaged inputs

- Sample masters: `i22-978003.nxs`, `i22-978008.nxs`, `i22-978013.nxs`, and
  `i22-978018.nxs`.
- Empty-cell background master: `i22-977723.nxs`.
- Detector, I0, beamstop-diode, and user-tetramm HDF5 sidecars for each master.
- SAXS/WAXS calibration and mask files in `data/processing/`.
- Recommended physical-correction pipelines:
  `pipelines/I22_SAXS_solids_operando.yaml` and
  `pipelines/I22_WAXS_solids_operando.yaml`.
- Test-only DAWN comparison pipelines:
  `pipelines/I22_SAXS_DAWN_crosscheck.yaml` and
  `pipelines/I22_WAXS_DAWN_crosscheck.yaml`.

The small NeXus master files contain relative external links to their HDF5
sidecars. Keep each master and its sidecars together in `data/`.

## Running the example

If `data/` is absent, first run `python tools/data_repository.py download
DLS/I22` from the repository root. Before the first Zenodo release,
contributors must obtain the development payload directly from the maintainers.

Start Jupyter from the repository root or this directory, select the prepared
MoDaCor kernel, and run `I22_solids_server_operando_preprocessed.ipynb` from top
to bottom. The notebook discovers the packaged files, writes compact
MoDaCor-facing files below `work/preprocessed/`, and writes processed results
below `work/output/`. Generated preprocessing links are relative, so the work
tree remains movable together with this example.

Preprocessing only reshapes or summarizes incompatible frame-wise metadata.
MoDaCor resolves detector geometry and corrections from the original NeXus
metadata and the packaged calibration files.

## Current validation status

Using MoDaCor 1.7.0, all four pipelines prepare successfully. The notebook
discovers exactly four packaged samples, validates matching `(1679, 1475)`
calibration/mask shapes, and preprocesses a sample/background pair with working
relative HDF5 links. The complete SAXS and WAXS server processing loop remains
a release-freeze validation task.

## Provisional values and release gates

- The absolute-intensity scalar `3.8e-15` is retained from the DAWN processing
  record for this example. Its provisional source is recorded in the notebook
  output metadata and next to the relevant DAWN pipeline operations.
- The DAWN cross-check pipelines reproduce selected recorded behaviour and are
  not the recommended physical-correction pipelines.
- The NeXus metadata include a proposal identifier, facility username, and an
  email-like title value. Permission and retention/sanitization must be
  resolved before public release.
