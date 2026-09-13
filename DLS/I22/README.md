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

The fourth chunking example starts a loopback-only Tiled service and requires
the `tiled-tests` MoDaCor extra. The notebook setup command includes it. The
full HDFSource and TiledSource transport comparisons are intentionally opt-in
because each configured validation performs 80 pipeline runs.

Preprocessing only reshapes or summarizes incompatible frame-wise metadata.
MoDaCor resolves detector geometry and corrections from the original NeXus
metadata and the packaged calibration files.

## Current validation status

Using MoDaCor 1.7.0, all four pipelines prepare successfully. The notebook
discovers exactly four packaged samples, validates matching `(1679, 1475)`
calibration/mask shapes, and preprocesses a sample/background pair with working
relative HDF5 links. Its chunk-validation section also exercises ten real SAXS
frames as five two-frame writes and verifies an exact array match against the
ordinary HDF output. This is an I/O assembly checkpoint; complete SAXS and WAXS
pipeline equivalence and the full server processing loop remain release-freeze
validation tasks.

The notebook also contains a server-driven example configured for all four
measurements and ten ten-frame chunks per measurement. It derives independent
SAXS and WAXS schemas from pilot chunks and stores two detector-specific run
groups in one physical HDF5 file. Each 40-chunk plan is initialized, populated,
inspected, and finalized independently. Lightweight trace events are retained
under `/processing/tracer/<run_name>/chunks/<chunk_id>/`. The complete
80-pipeline-run exercise remains an interactive beamline validation. A reduced
real-data smoke run has completed two ten-frame chunks for each detector in one
shared file and verified both pilot comparisons and trace manifests.

Two further opt-in examples run that workload with the server reading sample
slices directly. The first uses `HDFSource`; the second exposes the same
preprocessed files through a notebook-owned read-only Tiled server and uses
`TiledSource`. Typed source bindings apply one frame selection to the detector,
beamstop-diode mean and uncertainty, and detector count time. The finalized
HDFSource output is compared with the BufferSource result, and the TiledSource
output is compared with the HDFSource result, one stored chunk at a time. A
one-chunk real SAXS smoke run has completed through both direct transports,
including pipeline execution, trace publication, finalization, and exact
cross-transport comparison; the complete 80-run variants remain interactive.

The changing sample is chunked, but the background is deliberately handled as
one reusable aggregate per detector. Each pilot loads, corrects, and reduces
the complete background; later sample chunks reuse the reduced branch. The
complete detector stack and its cached/working copies, masks, uncertainties,
and numerical temporaries must therefore fit comfortably in worker memory.
SAXS and WAXS are processed sequentially and their sessions are deleted between
runs so the two background working sets do not coexist. A background that does
not fit requires a separate mergeable weighted-reduction workflow or an
explicit frame-pairing policy; averaging chunk means blindly is not sufficient.

## Provisional values and release gates

- The absolute-intensity scalar `3.8e-15` is retained from the DAWN processing
  record for this example. Its provisional source is recorded in the notebook
  output metadata and next to the relevant DAWN pipeline operations.
- The DAWN cross-check pipelines reproduce selected recorded behaviour and are
  not the recommended physical-correction pipelines.
- The NeXus metadata include a proposal identifier, facility username, and an
  email-like title value. Permission and retention/sanitization must be
  resolved before public release.
