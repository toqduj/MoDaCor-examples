# Diamond Light Source I22 example

This example demonstrates batch processing of operando SAXS and WAXS data from
Diamond Light Source beamline I22. Four sample measurements are paired with one
empty-cell background and detector-specific calibration and mask files.

## Packaged inputs

- Sample masters: `i22-978003.nxs`, `i22-978008.nxs`, `i22-978013.nxs`, and
  `i22-978018.nxs`.
- Empty-cell background master: `i22-977723.nxs`.
- Unobstructed-beam transmission reference: `i22-977723.nxs` for this data set.
  It is configured separately from the background in each notebook so another
  reference can be used, for example when the background is an empty capillary.
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
MoDaCor kernel, and choose one focused notebook:

- `I22_solids_server.ipynb` performs ordinary non-chunked batch processing.
- `I22_solids_chunked_buffer.ipynb` uploads notebook-sliced `BufferSource`
  chunks.
- `I22_solids_chunked_hdf.ipynb` lets the runtime slice `HDFSource` inputs.
- `I22_solids_chunked_tiled.ipynb` runs the same direct-slice workload through
  a notebook-owned Tiled service.

The old `I22_solids_server_operando_preprocessed.ipynb` filename is retained as
a short index so existing links lead to these notebooks. Shared discovery,
preprocessing, registrations, and explicit I22 plan construction live in
`i22_helpers.py`; the local Tiled lifecycle lives in `i22_tiled.py`.

The processing notebooks write compact MoDaCor-facing files below
`work/preprocessed/`. Ordinary results go below `work/output/`; chunked results
go below `work/chunk_server/`. Generated preprocessing links are relative, so
the work tree remains movable with this example. The Tiled notebook requires
the `tiled-tests` MoDaCor extra.

Preprocessing only reshapes or summarizes incompatible frame-wise metadata.
It averages channel 1 of both `bsdiodes` and `I0`, including their standard
errors on the mean. The unobstructed-beam measurement supplies the scalar
calibration ratio `mean(bsdiodes) / mean(I0)`. Frame-wise sample transmission
is `(bsdiodes / I0) / calibration_ratio`; its propagated SEM includes both
sample readouts and both reference readouts. The results are written to
`/entry1/sample/transmission` and `/entry1/sample/transmission_sem` in each
preprocessed file. MoDaCor resolves detector geometry and corrections from the
original NeXus metadata and the packaged calibration files.

The recommended SAXS and WAXS pipelines first normalize detector counts by
count time, then divide by sample transmission and incident `I0` flux. This
ordering keeps the non-integrating transmission and flux readouts downstream
of the integrating count-time correction.

## Current validation status

Using MoDaCor 1.8.0, all four pipelines prepare successfully. The shared helper
discovers exactly four packaged samples, validates matching `(1679, 1475)`
calibration/mask shapes, and preprocesses the samples/background with working
relative HDF5 links. Complete SAXS and WAXS pipeline equivalence and the full
server processing loop remain release-freeze validation tasks.

The BufferSource notebook is configured for all four
measurements and ten ten-frame chunks per measurement. It derives independent
SAXS and WAXS schemas from pilot chunks and stores two detector-specific run
groups in one physical HDF5 file. Each 40-chunk plan is initialized, populated,
inspected, and finalized independently. Lightweight trace events are retained
under `/processing/tracer/<run_name>/chunks/<chunk_id>/`. The complete
80-pipeline-run exercise remains an interactive beamline validation. A reduced
real-data smoke run has completed two ten-frame chunks for each detector in one
shared file and verified both pilot comparisons and trace manifests.

Two further notebooks run that workload with the server reading sample
slices directly. The first uses `HDFSource`; the second exposes the same
preprocessed files through a notebook-owned read-only Tiled server and uses
`TiledSource`. Typed source bindings apply one frame selection to the detector,
I0 mean and SEM, sample transmission and SEM, and detector count time. The finalized
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

## Supplementary poster visualizations and testing

The supplementary performance material under `supplementary/poster_2026/` is
retained to reproduce poster graphics and exploratory timing measurements. It
is not part of the main I22 correction example or its normal validation path.
`supplementary/poster_2026/I22_performance_benchmark.ipynb` compares
fresh-session server processing, sample-only partial reruns, and direct-slice
chunked-HDF throughput; its shared implementation is in the adjacent
`i22_performance.py`.

The benchmark uses the numerical form of the recommended pipelines: all
corrections and azimuthal integration are retained, while interactive plots and
intermediate 2D file sinks are removed. Preprocessing, server startup, session
construction, and the chunk-schema pilot are outside the reported timings. On
2026-09-15, an Apple M1 Max with 64 GiB RAM running Python 3.14.5 and MoDaCor
1.8.0 gave median end-to-end times of 29.2 s (full) and 9.24 s (sample rerun)
for SAXS, and 25.2 s and 6.14 s for WAXS. Forty direct-HDF ten-frame chunks per
detector gave median throughputs of 8.59 SAXS frames/s and 8.97 WAXS frames/s,
including chunk publication.

The notebook writes raw observations, per-step durations, machine metadata, and
PNG/SVG/PDF plots below
`work/supplementary/poster_2026/performance/`. This regenerable output tree is
ignored by Git. The processing date and MoDaCor version are also recorded in
the notebook so later benchmark runs can be distinguished from these results.

## Provisional values and release gates

- The absolute-intensity scalar `3.8e-15` is retained from the DAWN processing
  record for this example. Its provisional source is recorded in the notebook
  output metadata and next to the relevant DAWN pipeline operations.
- The DAWN cross-check pipelines reproduce selected recorded behaviour and are
  not the recommended physical-correction pipelines.
- The NeXus metadata include a proposal identifier, facility username, and an
  email-like title value. Permission and retention/sanitization must be
  resolved before public release.
