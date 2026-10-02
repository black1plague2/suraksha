# Agent log: new-rules (ITER-06) - phantom cargo + cross-document rules

## What was built
- `config.py`: weights `R_NO_VESSEL_CALL 0.35`, `R_DOC_MISMATCH 0.25`; settings `vessel_call_window_days=10`, `qty_tolerance=0.02`, `value_tolerance=0.05`, `standalone_review_threshold=0.25`.
- `agents/investigator.py`: `document_checks`, `document_gaps`, `vessel_call_check`, `doc_mismatch_check`.
- `agents/confidence.py`: `score(..., extra_evidence, extra_missing)`, `score_standalone` (band always LOW).
- `agents/report.py`: PART 5 sentences "Physical-cargo check: ..." / "Cross-document check: ..." with registry / document citations; both rules engage policy clause TF-4.1 (tag `collateral`).
- Stores: `vessel_calls`, `has_vessel_call_feed` on Protocol / MemoryStore / SnowflakeStore / RegistryCachedStore; `snapshot_registry` (6th SELECT) and `load_registry(vessel_calls=None)`; `store/batch.py` bulk read + `facts_row` (+2 columns, stand-alone rows with NULL match_type). Public names and signatures of batch.py unchanged.
- SQL: 01 (`REGISTRY.VESSEL_CALLS`), 04 (weights, facts columns + ALTERs for existing tables, `match_type` nullable, views, stand-alone never HIGH), 07 (truncate/load vessel_calls, `iter06-newrules`), 08 (`record()` writes the new facts incl. stand-alone rows), 11 (`iter06-batch-newrules`). `scripts/load_synth_to_snowflake.py` also loads vessel calls.
- Synth: deterministic vessel-call rows for every legitimate shipment (POL call ship-2d..ship, POD call); the new scenarios are generated AFTER the existing ones, with their own RNG and later request times, so all existing requests / ids / documents / txn ids are identical.
- Design guard: `has_vessel_call_feed()`. An empty feed never fires `R_NO_VESSEL_CALL` (a "check not run" ask is added instead). This is why the red-team suites (stores without a feed) are unchanged by construction.

## pipeline.py diff for master (also `docs/agent-logs/new-rules.pipeline.patch`; `git apply --check` is clean)
```diff
@@ class Suraksha.process, just before `if not matches:`
+        # Physical-cargo (vessel call) + cross-document rules: need no consortium match (ITER-06)
+        doc_ev = investigator.document_checks(req, fields, self.store, self.settings)
+        doc_gaps = investigator.document_gaps(req, fields, self.store)
+
         if not matches:
+            if doc_ev:  # stand-alone finding: never HIGH / never an auto-drafted STR; an analyst decides
+                conf = confidence.score_standalone(doc_ev, self.settings, request_id=rid, gaps=doc_gaps)
+                self.audit.append("system:investigator", "EVIDENCE_SCORED", rid,
+                                  {"score": conf.score, "band": conf.band.value,
+                                   "rules": [e.rule_id for e in conf.evidence], "standalone": True})
+                if conf.score >= self.settings.standalone_review_threshold:
+                    self.audit.append("system:pipeline", "EVIDENCE_REQUESTED", rid, {"missing": conf.missing})
+                    return done(PipelineStatus.NEED_MORE_EVIDENCE, None, conf)
             self.audit.append("system:pipeline", "CLEARED", rid, {"reason": "no consortium match"})
             return done(PipelineStatus.CLEAR)
@@ in the matches loop
-            conf = confidence.score(inv, self.settings)
+            conf = confidence.score(inv, self.settings, extra_evidence=doc_ev, extra_missing=doc_gaps)
```
Consequence: a `PipelineResult` can now be `NEED_MORE_EVIDENCE` with `investigation=None` (the Streamlit app already guards `r.investigation`; check any other consumer, e.g. slack / demo scripts).

## Verification (diff applied locally, then pipeline.py restored to its original)
- pytest: 383 passed (new `tests/unit/test_new_rules.py`: 20 tests). Without the pipeline diff, `tests/e2e::test_g1_detection_rate` and the pipeline-level tests in `test_new_rules.py` fail by design (they need the hook).
- eval seed 42: 192 requests, detection 100.0% (56/56), FPR 1.5% (2/136, the same two hard decoys), all goals PASS, 36 STRs, 0 citation errors.
  New rows: phantom_no_vessel_call NEED_MORE_EVIDENCE x5; doc_mismatch_qty NEED_MORE_EVIDENCE x4; doc_mismatch_value NEED_MORE_EVIDENCE x4; decoy_minor_rounding CLEAR x3; decoy_vessel_call_edge CLEAR x3. Existing scenario statuses unchanged.
- redteam v1: recall 27/27, FPR 0/14. v2: recall 24/24, FPR 0/8, 0 crashes.

## Needs live verification (Snowflake)
Re-run sql/01 (table), 04 (ALTER + views), 07 and 08 and 11 (re-deploy procs), refresh the git repo; `CALL LOAD_SYNTH(42)`, then `RUN_PIPELINE_BATCH(42, TRUE)` and the parity query (expect 0 mismatches over the larger set, including stand-alone rows: V_CONFIDENCE for `match_type IS NULL` is LOW with score = sum of the two weights). Confirm the trial account accepts `ALTER COLUMN match_type DROP NOT NULL` / `ADD COLUMN IF NOT EXISTS`, and that `SnowflakeStore.vessel_calls` (REGEXP_REPLACE key) runs. sql/12 / incremental.py must write facts for stand-alone rows through `batch.facts_row` (its tuple now has 11 columns).
