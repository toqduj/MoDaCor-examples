# B21 pipelines

`B21_frame_prefilter.yaml` is the chunk-safe first pass shared by batch-mode and
SEC-SAXS processing. It attaches the fixed mask and geometry, creates a
frame-wise threshold mask for negative and max-pegged detector values, creates
a non-destructive branch, and computes 50 coarse logarithmic I(q) bins per
frame. It deliberately stops before frame averaging and background subtraction.

`B21_frame_quality.yaml` is the compact second pass. After the chunked curves
for one complete measurement have been assembled, it loads those arrays and
runs `B21FrameQualityFilter` once. Its maximum high-q and minimum low-q
references are measurement-wide. Running it inside each raw-data chunk would
make the result depend on chunk boundaries.

The supplied DAWN pipeline remains unchanged at
`../data/processing/processing_pipeline_140926.nxs` as provenance and
cross-check material; it is not a recommended MoDaCor correction pipeline.
Its `0.0045–0.34 Å⁻¹` radial range is reused provisionally. The coarse bin
count and the B21 quality module's default low/high-q boundaries and 0.95/1.05
acceptance factors must be reviewed against representative batch and SEC-SAXS
runs before production use.

The later pipeline still needs an agreed sample/background selection policy,
normalization and correction order, flagged-frame handling, and final output
reduction. The B21 beamstop monitor is integrating and currently provides no
repeated readings from which to calculate a reliable SEM.
