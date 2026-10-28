# Project scope

- This is the independent, local video-phase and finding annotation project.
  Keep runtime code, state and assets inside this project or its configured local
  data directory. Do not import sibling checkouts or connect to the original
  gateway, storage API, SAM service or shared database.
- Preserve the extracted editor's time precision, overlap handling, annotation
  snapshots, conflict checks and undo/redo. Keep the common catalog and CSV
  field names compatible with exported records.
- Manual annotation must start without Torch or model weights. Load optional
  inference dependencies lazily, require explicit local checkpoints, and never
  download weights or substitute random models in runtime flows.
- AI results are proposals. Do not automatically overwrite manual annotations or
  mark them reviewed. Preserve explicit application, version checks and the
  protection for existing finding records.
- Preserve the reference feature function and adaptive FOV implementation when
  using checkpoints that validate their source hashes. Record source provenance
  in SOURCE_MANIFEST.json; architecture or preprocessing changes require explicit
  compatibility handling.
- Video files, weights, SQLite state, private records and generated artifacts
  belong outside Git. Tests use temporary directories and synthetic videos.
- The local task queue supports one application process. Mac MPS and real model
  performance require separate machine validation; passing browser or mocked
  policy tests does not establish model correctness.
