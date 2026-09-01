# Object Category Announcement PoC Validation

## Decision

Decision: proceed with production implementation under an explicit risk waiver.

This is a product decision to continue integration; it is not a technical pass of
the originally proposed quality gate. The NanoDet baseline remains below the
quality thresholds and must be treated as experimental until the model or
evaluation material is improved.

## Measured baseline

- Corpus: 32 page halves from the private, user-provided video source
- Primary pages: 23
- Primary accuracy: 26.1% (target: >= 80%)
- False announcement ratio: 65.6% (target: < 5%)
- Object inference p95: 22 ms on the development machine
- Peak RSS: 98 MB on the development machine
- Model: `object_detection_nanodet_2022nov.onnx`
- Model SHA384: `84ee6a6dd605f7019f25a81615a8fff886b235e8d3924930ca367c6e239a8c6d9c14a7e60b8bae54edca040cbf7b86e7`

## Scope of the waiver

- The object-announcement rule is integrated as a separately controllable
  capability and remains disabled by default for existing cameras.
- Person-entry behavior and stored data remain unchanged.
- Model-specific failures must degrade only the object rule; they must not make
  the application globally unhealthy when the object rule is disabled.
- The object model and private validation corpus are not redistributed by this
  report. The temporary local material should be removed after validation.
- A later model/corpus revision must rerun the PoC and replace this waiver with
  an evidence-backed technical decision before broad enablement.

## Reproduction

```text
python tools/object_detection_poc.py \
  --corpus /tmp/daihougou-object-validation \
  --object-model /tmp/daihougou-object-source/object_detection_nanodet_2022nov.onnx \
  --output /tmp/daihougou-object-validation/local-baseline.json
```

