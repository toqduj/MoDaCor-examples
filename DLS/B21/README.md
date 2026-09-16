# Diamond Light Source B21 example

This example is being developed around a BSA dilution series collected at
Diamond Light Source beamline B21. The acquisition contains an initial buffer,
six BSA concentrations, and a final buffer, with 21 one-second detector frames
per run.

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

Scientific processing is intentionally not implemented yet. The pipeline,
sample/background policy, normalization channels, correction order, output
schema, and DAWN comparison will be added after the processing strategy is
specified. The supplied DAWN calibration, mask, and processing file are
reference material only at this stage.

Run the notebook from the examples repository root or this directory with a
kernel containing the current MoDaCor checkout and its `server` extra, plus
`h5py` and `hdf5plugin`. Generated files will go below `work/`, which is ignored
by Git.

## Current validation status

Using MoDaCor 1.8.0, the clean notebook scaffold discovers all eight expected
measurements, validates the `(1, 21, 2167, 2070)` `uint32` detector stacks,
constructs 40 five-frame-or-smaller work items, and successfully starts and
stops its local runtime. The development manifest verifies all 211 packaged
files (368,610,024 bytes), and all B21 NeXus external links resolve in the
retained archive layout. Numerical processing validation awaits the pipeline.

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
