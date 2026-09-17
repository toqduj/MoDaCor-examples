# Diamond Light Source B21 example

This example is being developed around a BSA dilution series collected at
Diamond Light Source beamline B21. The acquisition contains an initial buffer,
six BSA concentrations, and a final buffer, with 21 one-second detector frames
per run.

## B21 acquisition modes and invariants

B21 has two relevant operating modes:

- In batch mode, an unchanging sample flows through the instrument capillary.
  The BSA dilution series in this example uses this mode. After quality
  filtering, accepted frames can be combined into one measurement average.
- In SEC-SAXS mode, size-exclusion chromatography precedes the SAXS exposure.
  A run typically contains 600–1000 frames, and every frame may represent a
  different part of the elution profile. Those frames must remain distinct
  after quality assessment rather than being treated as repeat observations
  of one unchanging sample.

B21 is a fixed-geometry instrument. Detector geometry is therefore treated as
invariant and can be calculated once, then reused for every measurement and
frame. B21 measurements include the capillary and solvent; there are currently
no companion measurements without the capillary or without solvent. In this
example, the two buffer runs are solvent-in-capillary measurements bracketing
the BSA series, not empty-beam or empty-capillary measurements. Downstream
background handling must respect that limitation.

The B21 beamstop monitor is an integrating monitor: its reading accumulates
over the detector exposure. It currently supplies no repeated sub-readings
from which a high-quality mean and standard error could be estimated. Its
normalization and uncertainty treatment will therefore differ from a
non-integrating monitor with repeated samples.

## Frame-quality pre-filter

Both acquisition modes start with the same pre-filter before frame averaging
or solvent/background subtraction:

1. Attach the fixed B21 geometry and instrument mask, then combine it with a
   frame-wise threshold mask for negative and max-pegged detector pixels.
2. Branch the detector stack into a pre-filter `ProcessingData` bundle.
3. Compute a coarse, frame-wise azimuthal integration using 50 logarithmic q
   bins and assemble those compact curves across all chunks belonging to one
   measurement.
4. Sum each assembled coarse curve in configured high-q and low-q regions.
5. Assign a `uint32` frame flag without removing the frame:
   - bit 0 (value `1`): high-q total is below `0.95 ×` the largest valid
     high-q total in the assessed batch;
   - bit 1 (value `2`): low-q total is above `1.05 ×` the smallest valid
     low-q total in the assessed batch;
   - value `0` is accepted and value `3` fails both tests.

The intended physical interpretation is that a high-q loss can indicate
radiation damage, while excess low-q intensity can indicate bubbles or flaring.
The q-region boundaries and numerical factors are configurable. The current
module defaults (`q ≤ 0.02 Å⁻¹`, `q ≥ 0.15 Å⁻¹`, `0.95`, and `1.05`) are
provisional and need validation on representative batch and SEC-SAXS data.

This is a two-pass chunked workflow. The direct-HDF pass reads and integrates
raw detector chunks. `B21FrameQualityFilter` then runs once over the assembled
50-bin curves for a complete measurement. It must not choose new reference
extrema independently in every detector chunk, because that would make flags
depend on chunk boundaries. Even a 1000-frame SEC-SAXS run produces only a
small `1000 × 50` second-pass input.

The branch is non-destructive and cheap: `CopyDataBundleKeys(copy: false)`
shares the read-only detector and fixed-geometry objects, so it does not copy a
complete image stack. `FramewiseIndexedAverager` then replaces only the
pre-filter branch with compact coarse curves. The main `sample` branch retains
the original 2D frames for subsequent correction, flagged-frame selection,
averaging, and subtraction.

`B21FrameQualityFilter` is intentionally instrument-specific. A future generic
quality/tagging framework may standardize how flags are recorded and consumed,
including a later anisotropy flag based on systematic angle-dependent residuals
between corrected 2D data and a remapped azimuthal average. That broader design
is outside the first pre-filter stage.

## Provisional settings to confirm with B21

The following values are explicit so they can be checked with beamline staff:

| Setting | Current value | Question to confirm |
| --- | --- | --- |
| Coarse integration | 50 logarithmic bins over 0.0045–0.34 Å⁻¹ | Is this sufficient for both batch and SEC-SAXS filtering? |
| Low-q region | q ≤ 0.02 Å⁻¹ | Is this the preferred bubble/flare region? |
| High-q region | q ≥ 0.15 Å⁻¹ | Is this the preferred radiation-damage region? |
| High-q acceptance | at least 0.95 of the run maximum | Should the reference be a maximum, robust percentile, or selected frame set? |
| Low-q acceptance | at most 1.05 of the run minimum | Should the reference be a minimum, robust percentile, or selected frame set? |
| Detector threshold | mask values below 0 or above 4294967293 | Are these the complete Eiger invalid-value conventions used at B21? |
| Buffer-equivalence limit | 1% RMS log-ratio distance | Should this be assessed before or after flux/monitor normalization, and over which q range? |

Batch and SEC-SAXS may ultimately require separate settings. In particular,
using extrema from a changing SEC elution sequence may need a different
reference policy from an unchanging batch measurement.

## Packaged inputs

| Run | NeXus title | Intended role |
| --- | --- | --- |
| `b21-478802` | `buffer_1` | Initial buffer |
| `b21-478803` | `bsa_10mgml` | BSA, 10 mg/mL |
| `b21-478804` | `bsa_5mgml` | BSA, 5 mg/mL |
| `b21-478805` | `bsa_2p5mgml` | BSA, 2.5 mg/mL |
| `b21-478806` | `bsa_1p25mgml` | BSA, 1.25 mg/mL |
| `b21-478807` | `bsa_0p6mgml` | BSA, 0.6 mg/mL |
| `b21-478808` | `bsa_0p3mgml` | BSA, 0.3 mg/mL |
| `b21-478809` | `buffer_2` | Final buffer |

The ignored `data/` tree retains the contributor's archive layout:

- `data/b21-*.nxs` and the adjacent `*-eiger*.h5` files are the raw GDA and
  Eiger data. Each NeXus master links to its adjacent Eiger master.
- `data/processed/` contains the DAWN-processed NeXus results and 21 exported
  ASCII curves per run. Each processed NeXus file links to its raw master one
  directory above.
- `data/processing/` contains the supplied DAWN processing pipeline,
  calibration, and mask.

These relative HDF5 links are why the archive is not divided into new `raw/`
and `reference/` directories. Keep the tree together when copying or
extracting it.

The source archive was supplied by the experimenter via
[Dropbox](https://www.dropbox.com/scl/fi/qmkn6yfv1sqrdycs32tmn/pauw2.tgz?rlkey=yyrdj83uui3kjdjwg5440ce5h&st=p0so2ojr&dl=0)
with permission to redistribute. The data listed in `data-manifest.json` are
declared under CC BY 4.0; see `DATA_LICENSE.txt` and `DATA_LICENSE.json`.

## Notebook

`B21_BSA_chunked_hdf.ipynb` initializes the portable workflow. It discovers
the eight runs from NeXus metadata, validates every raw detector stack and
relative link, constructs the 21-frame chunk schedule, and starts or reuses a
local MoDaCor runtime. The prepared source registrations deliberately select
server-side `HDFSource` access; detector chunks will therefore be sliced by the
runtime rather than loaded and uploaded by the notebook.

The first scientific stage is implemented in
`pipelines/B21_frame_prefilter.yaml`. Later sample/background selection,
normalization, correction order, flag-aware frame handling, output schema, and
DAWN comparison remain to be specified. The supplied DAWN calibration, mask,
and processing file remain reference material rather than a recommended
MoDaCor correction pipeline.

The current notebook run set contains both bracketing buffers and the 10, 2.5,
and 0.3 mg/mL BSA measurements. Raw stacks are processed in five-frame chunks,
assembled as compact `(1, 21, 50)` signal and Q arrays, and passed through
`pipelines/B21_frame_quality.yaml` once per complete measurement. Generated
prefilter and quality products are retained below the ignored `work/` tree.

The notebook renders the prepared pre-filter pipeline as a Mermaid graph. It
also defines a bounded frame-quality diagnostic for use after complete-run
curve assembly: representative accepted and rejected coarse I(q) curves are
shown beside their corresponding quick-masked 2D detector frames. The selected
images are read from HDF5 on demand, masked with the same fixed and threshold
criteria, and downsampled only for display, so this check does not materialize
a detector stack in notebook memory.

Run the notebook from the examples repository root or this directory with a
kernel containing the current MoDaCor checkout and its `server` extra, plus
`h5py`, `hdf5plugin`, and `matplotlib`. Generated files will go below `work/`,
which is ignored by Git.

## Current validation status

Using MoDaCor 1.8.0, the clean notebook discovers all eight expected
measurements, validates the `(1, 21, 2167, 2070)` `uint32` detector stacks,
constructs 40 five-frame-or-smaller work items, and successfully starts and
stops its local runtime. The eleven-step first pass combines the supplied
instrument mask with a frame-wise threshold mask for negative and max-pegged
Eiger values. Five selected runs have completed all 25 server-side detector
chunks and produced `(1, 21, 50)` coarse curves and Q coordinates. The compact
quality pass accepts all 21 frames in each run with the provisional thresholds;
observed high-q totals span 0.9885–1.0000 of their per-run maxima and low-q
totals span 1.0000–1.0202 of their per-run minima. The development manifest
verifies all 211 packaged files (368,610,024 bytes), and all B21 NeXus external
links resolve in the retained archive layout. Remaining three runs, broader
threshold validation, and later physical corrections remain open.

The initial/final buffer comparison uses all 21 accepted frames from each run.
Its RMS log-ratio distance is 0.659%, passing the provisional 1% practical
limit. The final/initial multiplicative scale is 0.99374 and the residual
shape-only distance is 0.193%. One individual bin reaches 1.139%, so not every
bin is inside ±1%. The combined-SEM standardized RMS distance is 4.21 (maximum
absolute z-score 6.96), meaning the highly repeatable curves are statistically
distinguishable even though their aggregate relative distance is below 1%.
Because this check precedes agreed flux and monitor normalization, it is not
yet sufficient on its own to certify that the capillary remained clean.

## Data and release notes

- Acquisition date: 2026-09-14.
- Facility visit identifier retained in the raw metadata: `cm44172-4`.
- The files contain the generic facility account `b21user`, sample positions,
  visit-local absolute source paths, detector serial metadata, and UUIDs. No
  personal name or email address was found in the reviewed NeXus string
  metadata.
- The contributor described the data as unrestricted for sharing. Creator
  spelling and the version-specific citation still need to be finalized in the
  Zenodo record before release.
- The data manifest is in development state; its Zenodo and archive fields are
  intentionally unset.
- Runtime and memory requirements will be recorded after the processing and
  output plan are defined and exercised.
