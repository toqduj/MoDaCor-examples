# Example-data policy

## Decision

Git contains notebooks, pipelines, documentation, validation tools, and one
`data-manifest.json` per implemented instrument. Measurement, calibration, and
mask payloads live in immutable per-instrument ZIP archives attached to a
versioned Zenodo record. Extracted `data/` directories are ignored by Git.

This separation keeps ordinary clones small while preserving a complete,
checksummed path from source examples to their exact data release.

## Archive unit

Each archive represents one instrument example and uses a stable name such as
`BAM-MOUSE-data.zip` or `DLS-I22-data.zip`. Its entries begin with `data/`, so
extracting it into the instrument directory recreates the layout expected by
the notebook. This also preserves the relative paths used by HDF5 external
links.

An instrument manifest records:

- the manifest schema and instrument identifier;
- the data-release version and version-specific Zenodo record/DOI;
- archive filename, download URL, byte size, and SHA-256 checksum;
- every extracted file's relative path, byte size, and SHA-256 checksum;
- the effective facility- or instrument-level data license and license-notice
  checksum.

Use version-specific Zenodo record URLs in manifests. A concept DOI may be
documented for discovery, but it must not replace the immutable version binding
used to reproduce an example.

## Development workflow

1. Place or update files in `<facility>/<instrument>/data/`.
2. Keep only the minimum representative data needed by the workflow.
3. Review embedded metadata and redistribution permission.
4. Add an applicable `DATA_LICENSE.json` and license notice or terms before
   packaging.
5. Run `python tools/data_repository.py update-manifests`.
6. Run `python tools/validate_repository.py --require-data --verify-data`.
7. Review and commit the changed manifest, notebooks, pipelines, and docs. The
   binary `data/` tree remains local and ignored.

## Release workflow

1. Freeze the instrument payloads and update all manifests.
2. Confirm that every manifest resolves an approved data license.
3. Run the full checksum and workflow validation.
4. Build per-instrument archives with
   `python tools/data_repository.py package --all --record-archive`.
5. Upload the archives from `dist/data/` to a Zenodo draft.
6. Enter each version-specific archive URL, record identifier, DOI, and data
   version in its manifest. Do not change recorded archive checksums.
7. Test downloads into a clean source checkout.
8. Publish the Zenodo version, commit the final manifests, and tag the source
   repository with the matching examples release.

If data change later, create a new Zenodo version and source tag. Never replace
an archive behind an existing manifest or published version-specific DOI.
