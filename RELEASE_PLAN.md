# Roadmap to the first Zenodo release

This plan keeps the repository usable while more instruments are added, then
freezes a reproducible archival snapshot only after the collection is complete.

## Phase 1 — Repository foundation (in progress)

- [x] Establish the facility/instrument directory convention.
- [x] Add root documentation and contributor guidance.
- [x] Keep generated notebook work separate from packaged inputs.
- [x] Convert the current MOUSE and I22 notebooks to archive-relative paths.
- [x] Add automated structural, portability, pipeline, and HDF5-link checks.
- [x] Strip notebook execution state through pre-commit and reject it during
  repository validation.
- [x] Keep Git lightweight and publish immutable per-instrument data archives
  through versioned Zenodo records.
- [x] License repository source materials under BSD-3-Clause by default and
  define facility/instrument override rules.
- [ ] Select and declare the applicable data license for every instrument.

## Phase 2 — Stabilize the current examples

- [ ] Rerun MOUSE from a clean copy and record its expected outputs/runtime.
- [ ] Rerun the complete I22 SAXS and WAXS server workflow from a clean copy.
- [ ] Reconcile all notebook prose with the tested MoDaCor release.
- [ ] Decide whether notebooks are released with all outputs cleared or with a
  small, consistent set of outputs produced from packaged data.
- [ ] Document every packaged input and the reason it is included.

## Phase 3 — Add instruments

For each new instrument:

- [ ] Add the required `README.md`, notebook, `pipelines/`, and representative
  `data/` layout.
- [ ] Make data discovery archive-relative and outputs disposable.
- [ ] Document facility acknowledgements, data provenance, correction scope,
  provisional constants, limitations, and expected results.
- [ ] Pass the repository validator and a focused end-to-end run.
- [ ] Update the root instrument catalogue.

## Phase 4 — Governance and public-data review

- [ ] Confirm permission to redistribute every dataset.
- [ ] Review embedded metadata for personal identifiers and decide whether to
  retain, document, or remove each field.
- [x] Add the root BSD-3-Clause license and hierarchical licensing policy.
- [x] Declare the BAM MOUSE data under CC-BY-4.0.
- [ ] Add approved facility/instrument data-license declarations and notices.
- [ ] Add authors, affiliations, ORCIDs, facility acknowledgements, funding,
  and citation guidance.
- [ ] Add `CITATION.cff` after the author order and release title are agreed.

## Phase 5 — Release freeze

- [ ] Stop content changes except release fixes.
- [ ] Pin one immutable MoDaCor release/tag/commit and record Python/dependency
  versions.
- [ ] Update and verify every `data-manifest.json`.
- [ ] Build one ZIP data archive per implemented instrument and record each
  archive's size and SHA-256 checksum in its manifest.
- [ ] Create a clean environment and run every notebook using only packaged
  files.
- [ ] Run `python tools/validate_repository.py --release` with no errors.
- [ ] Generate `SHA256SUMS` after all files are final.
- [ ] Create the archive while excluding `.DS_Store`, notebook checkpoints,
  virtual environments, caches, and `work/` directories.
- [ ] Extract the archive into a new temporary directory and repeat validation
  plus representative runs there.
- [ ] Review Zenodo title, abstract, creators, contributors, keywords, licenses,
  related identifiers, version, communities, and access conditions.
- [ ] Upload, verify Zenodo's file checksums, publish, and add the DOI and final
  citation back to this repository.

## Release gates currently requiring a decision

1. Data licensing: approved license for each facility/instrument payload.
2. Personal metadata: retain for provenance with permission, or sanitize.
3. Notebook outputs: fully cleared versus minimal verified reference outputs.
4. Final MoDaCor version and immutable revision.
