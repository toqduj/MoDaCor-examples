# BAM MOUSE example

This example demonstrates the standard solid-sample correction workflow for
the BAM MOUSE SAXS/WAXS instrument. It uses one zirconia-composite sample and
its associated empty-instrument background, each represented in ten instrument
configurations.

## Packaged inputs

- `data/MOUSE_20260903_2_*_stacked_modacor.nxs`: sample batch 2, configurations
  123, 125, 127, and 160–166.
- `data/MOUSE_20260903_1_*_stacked_modacor.nxs`: corresponding empty-instrument
  background batch 1.
- `pipelines/MOUSE_solids.yaml`: pipeline used by the notebook for the standard
  plate-like solid sample.
- `pipelines/MOUSE_solids_operando.yaml`: development pipeline for operando
  data with a separate static source; it is not used by the main notebook.

The files are already converted to the MoDaCor-ready stacked format. Geometry,
masks, flat fields, correction metadata, and measurement metadata are embedded
in each file. The upstream stacking/conversion procedure is described in the
notebook but is not part of this packaged example.

The data payload is licensed under CC BY 4.0; see `DATA_LICENSE.json` and
`DATA_LICENSE.txt`. Its final attribution will cite the version-specific Zenodo
record and DOI.

## Running the example

If `data/` is absent, first run `python tools/data_repository.py download
BAM/MOUSE` from the repository root. Before the first Zenodo release,
contributors must obtain the development payload directly from the maintainers.

Start Jupyter from the repository root or this directory, select the prepared
MoDaCor kernel, and run `MOUSE_solids_modacor.ipynb` from top to bottom. The
notebook finds `data/` and `pipelines/` automatically. Generated HDF5 results
and the server log are written below `work/output/`.

The current default processes sample batch 2. For each configuration it follows
the background reference embedded in the sample file and verifies that the
referenced background has the same configuration number.

## Current validation status

Using MoDaCor 1.8.0, repository validation passes, all ten sample/background
pairs resolve to matching configurations, and the 42-step pipeline prepares.
The complete ten-file runtime-server loop still needs to be rerun during the
release freeze, when its runtime and output inventory will be recorded.

## Supplementary poster visualizations and testing

The notebooks under `supplementary/poster_2026/` create additional poster
graphics and exploratory comparisons. They are retained for reproducibility,
but they are not part of the main MOUSE data-correction example or its normal
validation path:

- `supplementary/poster_2026/MOUSE_166_capillary_correction_maps.ipynb`
  visualizes the attenuation-aware empty-capillary subtraction change on
  curvilinear qx/qy coordinates.
- `supplementary/poster_2026/MOUSE_poster_pipeline_export.ipynb` creates
  raw/styled DOT, editable draw.io, SVG, and correction-impact badge data for
  the pipeline diagram.
- `supplementary/poster_2026/MOUSE_166_correction_eyecatcher.ipynb` and
  `supplementary/poster_2026/MOUSE_125_correction_eyecatcher.ipynb` create
  configuration-specific raw vs corrected comparisons and detector-resolved
  effect maps.
- `supplementary/poster_2026/MOUSE_all_configurations_correction_comparison.ipynb`
  creates the shared-axis comparison across all ten configurations, including
  ±1 SEM bands.

These notebooks write only regenerable artefacts below
`work/supplementary/poster_2026/figures/`. That output tree is ignored by Git.
The retained poster results were originally processed on 2026-09-15 using
MoDaCor 1.8.0; the same provenance is recorded inside every notebook so a later
run with a newer version can be identified clearly.

## Known limitations and release gates

- The notebook lists correction inputs whose uncertainty datasets are not yet
  available upstream.
- The main solids pipeline does not yet route capillary samples through the
  demonstrated capillary correction, and displaced-dispersant handling remains
  future work.
- Embedded proposal and user metadata include personal identifiers. Permission
  and retention/sanitization must be resolved before public release.
