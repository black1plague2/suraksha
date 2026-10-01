# ITER-03 — Snowflake-native deploy + blind red-team v2 hardening (2026-10-01)

## Team (parallel)
| Agent | Model | Delivered |
|---|---|---|
| snowflake-native | Sonnet | `sql/06_git_repo.sql` (Git repo + `EXECUTE IMMEDIATE FROM` deploy chain), `sql/07` LOAD_SYNTH proc, `sql/08` RUN_PIPELINE proc (+ PIPELINE_RESULTS / INVESTIGATION_FACTS for SQL↔Python rule parity), `sql/09` Streamlit-in-Snowflake, root `streamlit_app.py` shim + `environment.yml`, `docs/SNOWSIGHT_RUNBOOK.md`, 29 static SQL tests |
| red-team-v2 | Sonnet | fresh blind set, 41 scenarios (injection, missing docs, FX mismatch, unicode look-alikes, transshipment, splits, honest negatives) |
| injection-hardening | Sonnet | intake clips free-text fields + `detect_injection`; STR quotes/escapes document-derived values; cited PART 5 notice when a document contains instruction-like text |
| match-engine r3 | Sonnet | shared key `bln` (B/L + commodity) + private probe `bln_ref` from invoice "B/L Ref" → transshipment detection |
| demo-polish | Haiku | names next to ids, readable ownership chains, "why this matters" |

## Results
| Check | Result |
|---|---|
| `python -m pytest tests -q` | **322 passed** |
| `scripts/eval.py` (seed 42) | detection 100%, FPR 1.5%, 36 STRs, 0 citation errors, all goals PASS |
| red-team v1 (`scripts/redteam_eval.py`) | recall 100% (27/27), FPR 0% |
| red-team v2 before fixes | recall 95.8% (23/24), FPR 0%, 0 crashes; 2 injection-echo invariant failures |
| red-team v2 after fixes | recall **100%** (24/24), FPR **0%** (0/8), 0 crashes |

## Live Snowflake progress (user, in Snowsight)
- Repo made public by the user (secret scan of tracked files + history clean beforehand).
- User ran the bootstrap worksheet: API integration `github_api` + Git repository `SURAKSHA.CORE.SURAKSHA_REPO`; `LS .../branches/main/sql/` lists 00–09 ✅ (screenshot = first BUILD evidence).
- Next: Cortex Code PLAN prompt → BUILD (00–05, 07–09) → RUN (`CALL LOAD_SYNTH(42)`, `CALL RUN_PIPELINE(42)`, open app) → TEST (parity query, audit verify).

## Security / privacy decisions
- Permission classifier blocked (a) Claude flipping repo visibility → user did it; (b) committing Snowflake account
  identifiers → scrubbed to `<ORG-ACCOUNT>` placeholders; identifiers kept only in local (non-repo) notes.
- Untrusted document text is now always quoted in STRs and never interpreted; matters once AI_EXTRACT/AI_COMPLETE read documents.

## Master fixes during integration
- `ExtractedFields.bl_ref` added; Windows-safe log file handling (no in-process rotation).
- `test_injection_logs_warning_with_ids`: attach caplog handler to the non-propagating `suraksha` logger.

## Known gaps → ITER-04
1. Several Snowflake items "verify live" (COPY FILES from git stage, Snowpark connection shim, GRANT READ ON GIT REPOSITORY, SiS runtime type) — resolve from CoCo BUILD errors.
2. Streamlit-in-Snowflake app still uses the in-memory backend; wire SnowflakeStore so the app reads live tables.
3. Consolidate invoice "B/L Ref" parsing into intake (fingerprint has a temporary local regex).
4. red-team v2 has now been used to tune — no longer blind.
