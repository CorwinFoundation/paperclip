# Synthetic voice E2E adapter seam

BLUF: `run_synthetic_voice_e2e.py` proves a metadata-only voice source can cross the existing `source.SourceDriver` boundary, unchanged Collector pipeline, contract-v1.0 receiver, and Log Tracker without emitting call content.

An Addendum A platform adapter implements `SourceDriver` and maps vendor events to the generic fields used by `SyntheticVoiceDriver`: event identity/time, session and deployment identity, event class, outcome, and pinned producer versions. Platform SDK types remain behind the adapter. Transcript, audio, recording, spoken-PII, call-content, and generic content fields are prohibited recursively and fail before Collector emission.

Swap procedure: implement a platform-specific driver in its own package, pin its driver/mapping/source versions, run these invariants against synthetic fixtures, then replace only driver construction. Do not change Collector core, receiver contract-v1.0, billing state, or Log Tracker interfaces.
