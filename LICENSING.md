# Licensing structure

The repository separates licenses for source materials from licenses for
measurement and calibration data.

## Source materials

The root [LICENSE](LICENSE) applies under BSD-3-Clause to tracked notebooks,
pipeline YAML files, scripts, manifests, and documentation unless a more
specific license is present.

License precedence follows the directory hierarchy:

1. An instrument-level `<facility>/<instrument>/LICENSE` applies within that
   instrument directory.
2. Otherwise, a facility-level `<facility>/LICENSE` applies within that
   facility directory.
3. Otherwise, the root BSD-3-Clause license applies.

A more specific license must contain the complete applicable license text and
its README should explain the scope. Do not modify the text of a standard SPDX
license; put scope and attribution details in the README instead.

## Data

The root BSD-3-Clause license does **not** license measurement, calibration,
mask, or other files distributed through the ignored `data/` directories or
Zenodo data archives. Those files require an explicit data-license declaration.

Data-license precedence is:

1. `<facility>/<instrument>/DATA_LICENSE.json` for an instrument override.
2. `<facility>/DATA_LICENSE.json` as the facility default.
3. No license and therefore no public data package.

Each declaration has this form:

```json
{
  "schema_version": 1,
  "spdx_id": "CC-BY-4.0",
  "name": "Creative Commons Attribution 4.0 International",
  "license_url": "https://creativecommons.org/licenses/by/4.0/legalcode",
  "license_file": "DATA_LICENSE.txt",
  "attribution": "Please cite the version-specific Zenodo record and indicate whether changes were made."
}
```

`license_file` is relative to the declaration. For a standard public license it
may contain a concise license and attribution notice with a canonical legal-code
URL; custom licenses must include their complete terms and use an unambiguous
`LicenseRef-...` identifier. `license_url` and `attribution` are optional, but
recommended. The manifest tool records the effective declaration, attribution,
and notice checksum in each instrument's `data-manifest.json`.

The packaging command refuses to build a publishable data archive without a
valid declaration. It embeds the resolved notice or text as `DATA_LICENSE.txt`
so the license accompanies archives downloaded directly from Zenodo.

## Contributions and third-party material

Contributors must have authority to offer their source contributions under the
applicable directory license and to redistribute data under the declared data
license. Third-party material retains its own license and must be identified in
the nearest README and, when appropriate, in a dedicated notice file.
