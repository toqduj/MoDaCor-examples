# BAM SAXSess-I N008 reference example

This example processes six aliquots of BAM reference material N008 measured on
SAXSess-I on 2026-06-08. It starts from the original multi-frame Mythen2 TIFFs,
derives the manually acquired transmission and relative-flux quantities, and
runs the full MoDaCor correction graph to produce one absolute-intensity
`I(Q)` curve per aliquot.

## Packaged inputs

`data/20260608_N008/metadata_N008.xlsx` records the measurement relationships
and sample metadata. The compact payload contains exactly the TIFF dependency
closure named by that workbook:

- N008 aliquots `S00635` and `S00637` through `S00641`;
- water `S00632`, used as both sample background and intensity calibrant;
- empty capillary `S00631`, used as the water background;
- empty chamber `S00628`, the root of the background tree; and
- foil-fluorescence measurements `S00629`, `S00630`, `S00633`, and `S00636`
  for relative flux and the successive transmission ratios.

`data/calibration/water_cal_reference.pdh` is the absolute water reference
profile imported from the legacy SAXSQuant workflow. It is constant at
`1.641 1/(m sr)` with a stated 1% uncertainty over the supplied q range. The
original measurement dump also contained unrelated measurements, generated
HDF5/results, incomplete comparison products, and a second SAXSess instrument;
those are intentionally not part of this example.

The data payload is licensed under CC BY 4.0; see `DATA_LICENSE.json` and
`DATA_LICENSE.txt`. The workbook contains BAM organisational-unit and storage-
location fields but no person names or email addresses.

## Processing scope

`saxsess_helpers.py` converts the TIFFs and workbook into compact HDF5 sources
below `work/preprocessed/`. Preprocessing is limited to acquisition-format
translation and quantities that must be reconstructed from this manual
instrument: beam-centre location, foil fluorescence, the chained transmission,
relative flux, and measurement metadata.

`pipelines/SAXSess_I_N008_absolute.yaml` then applies:

1. Poisson uncertainties, exposure-time normalization, dark-current
   subtraction, relative-flux normalization, and transmission normalization;
2. frame averaging and water-background subtraction;
3. detector geometry, solid-angle correction, and thickness normalization;
4. water-based absolute-intensity calibration over `2–4 nm^-1`;
5. logarithmic azimuthal averaging over `0.1–6 nm^-1`; and
6. combined q/intensity uncertainties and CSV/HDF5 export.

The expected CSV columns are q, q uncertainty, absolute intensity, and
intensity uncertainty, with units `1/nm`, `1/nm`, `1/(m sr)`, and `1/(m sr)`.

## Running the example

If `data/` is absent, first run `python tools/data_repository.py download
BAM/SAXSess_I` from the repository root. Before the first Zenodo release,
contributors must obtain the development payload directly from the
maintainers.

Use MoDaCor 1.8.0 with the `server` extra, plus pandas, openpyxl, Pillow,
PyYAML, h5py, and matplotlib. Start Jupyter from the repository root or this
directory and run `SAXSess_I_N008_absolute.ipynb` from top to bottom.

The notebook launches a loopback-only MoDaCor runtime by default. To use an
existing runtime, set `RUNTIME_URL` in the configuration cell. If that runtime
runs on another machine, also set `RUNTIME_PROJECT_DIR` to the server-visible
path of this example directory; both machines must see the same files. The
notebook never stops an externally managed runtime.

Generated HDF5 sources, absolute CSV/HDF5 results, and the local server log are
written below `work/`. On the development machine the complete batch takes
about three seconds after imports and preprocessing; later aliquots reuse the
unchanged water/background/calibration branches.

## Current validation and release gates

With MoDaCor 1.8.0, the 53-step pipeline prepares and the complete six-aliquot
batch succeeds. Every output contains 98 finite q/intensity pairs over
approximately `0.103–5.88 nm^-1`, in absolute units of `1/(m sr)`. The first
run is full and all five later runs use partial invalidation as intended.

The instrument constants in `config/SAXSess_I.yaml` were carried over from the
supplied legacy notebook/static YAML and remain provisional until checked
against the instrument log. The water-reference value and its authorship need
an exact source citation before release. The pipeline does not apply a separate
capillary self-absorption correction; N008 and its water background share the
same 1 mm capillary geometry.
