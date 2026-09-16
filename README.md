# MoDaCor instrument examples

This repository is the canonical collection of instrument-specific MoDaCor
application examples. Each example combines a runnable notebook, one or more
pipeline YAML files, and enough representative data to exercise the workflow.

The collection is under active development. It is not yet the frozen Zenodo
release: more instruments will be added, licensing and personal-metadata
decisions remain open, and the final release will pin an exact MoDaCor version.

## Instrument catalogue

| Facility | Instrument | Current contents | Status |
| --- | --- | --- | --- |
| BAM | MOUSE | Ten-configuration sample/background pair and solids pipelines | Runnable example |
| DLS | I22 | Four SAXS/WAXS measurements, background, calibration, masks, and four focused notebooks | Runnable batch plus Buffer/HDF/Tiled chunk demonstrations; full archive run pending |
| BAM | SAXSess I | Placeholder | Planned |
| BAM | SAXSess II | Placeholder | Planned |
| DLS | B21 | BSA dilution series, DAWN references, and chunked-HDF notebook scaffold | Processing design pending |
| DLS | DL-SAXS | Placeholder | Planned |
| TU Graz | To be defined | Placeholder | Planned |

## Repository layout

Every implemented example follows this layout:

```text
<facility>/<instrument>/
├── README.md
├── <workflow>.ipynb
├── data-manifest.json
├── data/                 # downloaded, ignored by Git
├── pipelines/
└── work/                 # generated locally; not versioned or archived
```

Raw NeXus/HDF5 files may use relative external links to sidecar files. Binary
data are distributed as versioned, per-instrument Zenodo archives rather than
through Git. Keep the contents of an instrument's `data/` directory together
when copying or extracting an example.

See [CONTRIBUTING.md](CONTRIBUTING.md) for the contract used by new instrument
examples and [RELEASE_PLAN.md](RELEASE_PLAN.md) for the route to the first
Zenodo release.

## Licensing

Tracked repository source materials use BSD-3-Clause by default. Facilities and
instruments may provide a closer `LICENSE` that overrides that default within
their directory. Data are never covered implicitly by the source license; each
published payload must resolve an explicit facility- or instrument-level data
license. See [LICENSING.md](LICENSING.md) for precedence and declaration rules.

## Development environment

The current development baseline is MoDaCor 1.8.0. The final Zenodo release
will record an immutable MoDaCor tag or commit after every included example has
been rerun against it. MoDaCor requires Python 3.12 or newer.

Create the environment in this examples repository and install the MoDaCor
revision that the examples should exercise. Python 3.14 is used for current
development:

```bash
uv venv --python 3.14 .venv
source .venv/bin/activate
uv pip install -e "/path/to/MoDaCor[server,attenuation,plotting]" matplotlib ipykernel hdf5plugin
uv pip install pre-commit
python -m ipykernel install --user --name modacor-examples --display-name "Python (MoDaCor examples)"
```

After cloning this examples repository, enable its tracked hooks once:

```bash
pre-commit install
```

The notebook hook removes outputs, execution counts, widget state, and transient
execution metadata before commit. If it changes a notebook, review and stage
the cleaned file again. Repository validation independently rejects remaining
execution state, providing a CI/release backstop.

Start Jupyter from this repository root or from an individual instrument
directory. The notebooks locate their packaged `data/` and `pipelines/`
directories automatically and write generated files below `work/`.

## Obtaining example data

Each implemented instrument has a tracked `data-manifest.json`. It records the
immutable Zenodo archive, archive checksum, and checksums of all extracted
files. After the first data release is published, download one instrument with:

```bash
python tools/data_repository.py download BAM/MOUSE
python tools/data_repository.py download DLS/I22
```

Use `download --all` to retrieve every published instrument dataset. During
development, before Zenodo URLs are assigned, contributors can populate the
ignored `data/` directories directly and update their manifests with
`python tools/data_repository.py update-manifests`.

## Validation

With the MoDaCor development environment active, run:

```bash
python tools/validate_repository.py
```

This checks repository hygiene, notebook portability, pipeline preparation,
data-manifest structure, and the HDF5 external links of any locally available
data. A source-only checkout therefore remains testable. Before release, use
`--require-data --verify-data` to require every payload and calculate its
checksums. `--release` additionally enforces resolved data licenses, frozen data
versions, archive checksums, and version-specific Zenodo bindings. A clean
validator run is necessary but not sufficient for release; the full instrument
workflows must also be rerun from a clean extracted copy.

## Data volume and storage

The current working collection is approximately 1.8 GB and will grow as more
instruments are added. The Git repository deliberately remains lightweight:
it ignores extracted `data/` trees and tracks their manifests. Each instrument
is packaged as a separate ZIP archive so users can download only the examples
they need. [DATA_POLICY.md](DATA_POLICY.md) defines the versioning, integrity,
and release workflow.

## Release status

Do not cite this working tree as a released dataset. Citation text, authors,
licenses for code and data, facility acknowledgements, checksums, and the
Zenodo DOI will be added during the release-freeze phase.
