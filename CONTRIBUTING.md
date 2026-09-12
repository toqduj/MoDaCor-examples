# Adding an instrument example

Instrument examples should be understandable and runnable after extracting the
repository on a different computer. Avoid assumptions about a contributor's
home directory, original measurement directory, or active Git branch.

## Required structure

Create `<facility>/<instrument>/` containing:

- `README.md`: purpose, sample/background selection, data provenance,
  correction scope, known limitations, expected outputs, and an approximate
  runtime/resource note.
- One or more notebooks with archive-relative discovery and a clean execution
  state.
- `pipelines/` containing descriptively named YAML pipelines.
- A tracked `data-manifest.json` describing the smallest representative dataset
  that still exercises the important instrument behaviour.
- A local, ignored `data/` tree whose layout exactly matches the corresponding
  per-instrument Zenodo archive.

Generated files belong below `<facility>/<instrument>/work/`; notebooks must
not write results into `data/` or `pipelines/`.

## Notebook rules

- The notebook must run when Jupyter starts either at repository root or at the
  instrument directory.
- Do not embed `/Users/...`, `/home/...`, drive-letter user paths, or paths to
  an original measurement tree.
- Keep preprocessing lean. Reshape or summarize incompatible metadata, and let
  MoDaCor modules resolve geometry and corrections where possible.
- Commit notebooks without outputs or execution state. Install the tracked
  pre-commit hook with `pre-commit install`; it strips these fields
  automatically. Never retain output from a larger private dataset.
- State the compatible MoDaCor release or commit and any optional dependencies.
- Mark cross-check or provisional pipelines clearly so they cannot be mistaken
  for the recommended physical correction.

## Pipeline and data rules

- Pipelines must prepare successfully under the repository's pinned MoDaCor
  version.
- External HDF5 links must be relative and all targets must be included.
- Document the source and provisional status of imported constants next to the
  relevant YAML or notebook setting.
- Record why every sample/background/calibration file is included. Prefer a
  compact, representative subset over an unbounded measurement dump.
- Inspect NeXus/HDF5 metadata for names, usernames, email addresses, proposal
  identifiers, sample sensitivity, or other material requiring permission.
- Add an approved `DATA_LICENSE.json` and license notice or terms at facility
  or instrument level. Instrument declarations override facility defaults;
  the root source license never licenses data implicitly.
- Never add extracted binary data to ordinary Git history. Run
  `python tools/data_repository.py update-manifests` after changing a local
  payload and review the resulting manifest.

## Before requesting inclusion

Run `python tools/validate_repository.py --require-data --verify-data`, execute
the focused workflow from a fresh environment, and record the validation result
in the instrument README. The release checklist in `RELEASE_PLAN.md` applies to
the collection as a whole.
