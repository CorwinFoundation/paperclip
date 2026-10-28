BEAAA-2299 offline equivalence oracle

pre_refactor_emitter.py is an unchanged copy of harness/p2_emit.py from
649db755432cc9bfeac7f090917bdcff4e88f50c (before bounded orchestration).
Tests supply explicit current corpus/policy paths, avoiding fixture-relative
path resolution, and deterministic offline runners. The real aggregate
implementations run in both paths. Five bounded executions per applicability
state must produce identical serialized measurements and details.

This establishes orchestration equivalence against the old implementation.
It does not establish equivalence to historical B2 live-model output: the
BEAAA-2252 p2_emission.json is absent from the expected artifact directory.
No model requests are made by this test.
