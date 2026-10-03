# Suraksha Runbook: Local Setup & Deployment

## Local Quickstart

**Requirements:** Python 3.11+, `pip` or `uv`.

### 1. Install

```bash
cd /path/to/suraksha
pip install -e ".[dev]"
```

Optional: For Snowflake testing, install extras:
```bash
pip install -e ".[dev,snowflake,app]"
```

### 2. Run unit tests

```bash
python -m pytest tests/unit -q
```

Expected: all pass. Example output:
```
tests/unit/test_intake.py::test_extract PASSED
tests/unit/test_fingerprint.py::test_build_fingerprint PASSED
...
5 passed in 0.23s
```

### 3. Run evaluation (memory backend)

```bash
python scripts/eval.py
```

This generates 110 synthetic cases (60 clean, 30 duplicates, 20 decoys), runs the full pipeline on each using the in-memory store, and reports:
- Detection rate (≥90% target)
- False positive rate (<10% target)
- Mean latency per request (<5 min target)
- Per-case breakdown (status, rule scores, audit records)

Output: `logs/eval_report.json` (or stdout JSON).

### 4. Run demo (optional, interactive)

```bash
python scripts/demo.py
```

Load a single duplicate case interactively; step through intake, match, investigation, and approval.

---

## Snowflake Deployment

### Prerequisites

- Snowflake account with cross-region inference enabled (Cortex AI)
- `SNOWFLAKE_ACCOUNT`, `SNOWFLAKE_USER`, `SNOWFLAKE_PASSWORD`, `SNOWFLAKE_ROLE` in env or `~/.snowflake/connections.toml`
- Role: `ACCOUNTADMIN` or custom role with warehouse/database/schema/stage create grants

### Deploy Steps

1. **Create warehouse and schemas** (sql/00_setup.sql)
   ```bash
   snowsql -c <connection_name> -f sql/00_setup.sql
   ```
   Creates: SURAKSHA database, CORE/REGISTRY/CONSORTIUM/BANK_A/B/C schemas, roles.

2. **Create tables** (sql/01_tables.sql)
   ```bash
   snowsql -c <connection_name> -f sql/01_tables.sql
   ```

3. **Set up secure data sharing** (sql/02_consortium_share.sql)
   ```bash
   snowsql -c <connection_name> -f sql/02_consortium_share.sql
   ```
   Banks insert their entries via stored procedure; Suraksha reads via shared view.

4. **Set up audit immutability** (sql/03_audit_immutability.sql)
   ```bash
   snowsql -c <connection_name> -f sql/03_audit_immutability.sql
   ```
   Audit table is append-only; VERIFY_CHAIN view checks hash integrity.

5. **Create confidence rule views** (sql/04_rules.sql)
   ```bash
   snowsql -c <connection_name> -f sql/04_rules.sql
   ```
   SQL views mirror Python config.RULE_WEIGHTS; used for scoring.

6. **Stage Cortex AI resources** (sql/05_cortex.sql)
   ```bash
   snowsql -c <connection_name> -f sql/05_cortex.sql
   ```
   Creates PDF stage and AI_EXTRACT/AI_COMPLETE functions for document intake and STR narrative.

7. **Load synthetic data**
   ```bash
   SNOWFLAKE_ACCOUNT=... SNOWFLAKE_USER=... python scripts/load_synth_to_snowflake.py
   ```
   Populates SURAKSHA.CORE (registry) and SURAKSHA.BANK_A/B/C (requests).

8. **Deploy Streamlit app** (optional)
   ```bash
   # In Snowflake console or via Python SDK:
   snowflake-cli streamlit deploy app/streamlit_app.py
   ```

### Full deployment reference

See [SNOWFLAKE_DEPLOY.md](SNOWFLAKE_DEPLOY.md) for advanced topics: multi-bank consortium setup, schema inheritance, complex secure sharing, Cortex quota management.

---

## Slack Integration (Approval Notifications)

### Setup

1. **Create an incoming webhook** in your Slack workspace:
   - Go to: `https://api.slack.com/apps` → Create New App → From scratch
   - Name: "Suraksha Approval"
   - Workspace: your team's workspace
   - Features → Incoming Webhooks → Create New Webhook to Channel
   - Copy the webhook URL

2. **Enable interactivity** (for Approve/Reject buttons):
   - In your Slack app settings: Interactivity & Shortcuts → Interactivity toggle ON
   - Request URL: `https://<your-domain>/slack/actions` (your backend endpoint)
   - Signing Secret: copy it

3. **Set environment variables:**
   ```bash
   export SURAKSHA_SLACK_WEBHOOK="https://hooks.slack.com/services/..."
   export SURAKSHA_SLACK_SIGNING_SECRET="xxx"
   ```

4. **Backend setup** (Flask/FastAPI example):
   ```python
   from suraksha.integrations.slack import handle_action
   
   @app.post("/slack/actions")
   def slack_action():
       payload = request.json
       case = handle_action(payload, store, audit_log)
       return {"ok": True}
   ```

When a case reaches HIGH confidence, the system posts a Block Kit message to Slack. Officers click Approve/Reject; action updates the case in the database and logs the decision in the audit trail.

---

## Environment Variables

Drawn from `src/suraksha/config.py`:

| Variable | Default | Purpose |
|----------|---------|---------|
| `SURAKSHA_SALT` | `"dev-only-salt-change-me"` | Consortium fingerprint salt (change for production) |
| `SURAKSHA_QTY_BAND_MT` | `50` | Quantity band width (metric tonnes) for fuzzy matching |
| `SURAKSHA_CONFIDENCE_THRESHOLD` | `0.6` | Score threshold: ≥ threshold → HIGH → draft STR |
| `SURAKSHA_TIMING_WINDOW_DAYS` | `45` | Days: pledges within this window count as timing overlap |
| `SURAKSHA_MAX_GRAPH_HOPS` | `4` | Max BFS hops in ownership graph (directors, UBOs, addresses) |
| `SURAKSHA_BACKEND` | `"memory"` | Backend: "memory" (tests) or "snowflake" (prod) |
| `SURAKSHA_REPORTING_ENTITY` | `"Bank A (synthetic)"` | Reporting bank name in STR reports |
| `SURAKSHA_PRINCIPAL_OFFICER` | `"Principal Officer (synthetic)"` | Officer signature in STRs |
| `SURAKSHA_SLACK_WEBHOOK` | (unset) | Slack incoming webhook URL for approval messages |
| `SURAKSHA_LOG_LEVEL` | `"INFO"` | Log verbosity (DEBUG, INFO, WARNING, ERROR) |
| `SURAKSHA_LOG_DIR` | `logs/runtime` | Directory for suraksha.jsonl (rotated logs) |

Snowflake-specific (when `SURAKSHA_BACKEND="snowflake"`):
| Variable | Purpose |
|----------|---------|
| `SNOWFLAKE_ACCOUNT` | Snowflake account identifier (e.g. "xy12345.region.cloud") |
| `SNOWFLAKE_USER` | Username |
| `SNOWFLAKE_PASSWORD` | Password (or use connections.toml) |
| `SNOWFLAKE_ROLE` | Role with warehouse/database access |
| `SNOWFLAKE_WAREHOUSE` | Warehouse name (default: auto-provision) |

---

## Troubleshooting

### "ModuleNotFoundError: No module named 'suraksha'"
- Run `pip install -e ".[dev]"` from the repo root (with the dot).

### pytest: "no tests ran"
- Check pythonpath in pyproject.toml includes `src`.
- Run from repo root: `python -m pytest tests/unit -q`.

### Snowflake connection fails ("Incorrect username/password")
- Verify credentials in env or `~/.snowflake/connections.toml`.
- Check warehouse exists and you have USAGE grant.
- Run: `snowsql -c <connection> -q "SELECT 1;"` to test.

### Cortex AI_EXTRACT returns empty fields
- Ensure the PDF stage is writable and PDFs are uploaded.
- Check Cortex quota (Cortex AI in Snowflake is consumption-based).
- See [sql/05_cortex.sql](../sql/05_cortex.sql) for AI_EXTRACT syntax.

### Slack buttons (Approve/Reject) don't work
- Verify interactivity is enabled in the Slack app and request URL is reachable.
- Check `SURAKSHA_SLACK_SIGNING_SECRET` matches Slack app settings.
- Inspect logs: `SURAKSHA_LOG_LEVEL=DEBUG python scripts/...`.

### Audit hash chain verification fails ("prev_hash mismatch")
- Do NOT manually edit audit records.
- Check database integrity: `SELECT * FROM suraksha.core.audit ORDER BY seq;`.
- If integrity is broken, contact the system admin; cases are immutable as-is.

### Fingerprint hash doesn't match expected value
- Verify `SURAKSHA_SALT` is the same across all runs.
- Check quantity normalization: `qty_band = round(qty_mt / SURAKSHA_QTY_BAND_MT)`.
- Commodity canonicalization: "HR STEEL COILS" → "STEEL_COILS_HR" (see synth/generator.py).

---

## Debugging

**Enable detailed logs:**
```bash
export SURAKSHA_LOG_LEVEL=DEBUG
python scripts/eval.py
tail -f logs/runtime/suraksha.jsonl
```

**Inspect pipeline state:**
```python
from suraksha.store.memory import MemoryStore
from suraksha.synth import generate, load_into

ds = generate(seed=42)
store = MemoryStore()
load_into(store, ds)

# Spot-check a request
req = ds.requests[0]
print(req)
```

**Manual Snowflake query:**
```bash
snowsql -c <connection> -d suraksha -q "SELECT * FROM core.companies LIMIT 5;"
```

---

## Testing Strategy

**Unit tests** (`tests/unit/`): Each agent module (intake, fingerprint, investigator, confidence, report, approval) has isolated tests using the memory store. No external services needed.

**E2E tests** (`tests/e2e/`): Full pipeline on synthetic dataset, using memory store. Runs in CI; validates G1–G5 goals.

**Snowflake integration tests** (marked `@pytest.mark.snowflake`): Require live Snowflake connection. Skipped by default. Run with:
```bash
pytest tests/unit -k snowflake --snowflake
```

**Eval script** (`scripts/eval.py`): 110 synthetic cases end-to-end; reports detection rate, FPR, latency. Used for G1/G2 acceptance.

---

## Next Steps

1. Run `python scripts/eval.py` locally to verify the core pipeline works.
2. Deploy to Snowflake using the SQL scripts and `scripts/load_synth_to_snowflake.py`.
3. Set up Slack webhook for approval notifications (optional).
4. For judges: follow [COCO_USAGE.md](COCO_USAGE.md) to demonstrate plan → build → run → test using CoCo CLI prompts.

See [ARCHITECTURE.md](ARCHITECTURE.md) for system design and [CONTRACTS.md](CONTRACTS.md) for module ownership.

## Public demo (Streamlit Community Cloud)

The public demo at **https://suraksha01.streamlit.app/** is deployed straight from this repo's `main` branch:

- Main file: `streamlit_app.py` (repo root). Packages: `requirements.txt` (Streamlit in Snowflake uses `environment.yml` instead).
- It runs the app on built-in demo data (memory backend) — no Snowflake connection and no credentials online.
- Every push to `main` redeploys it automatically within a minute or two.

**Keeping it awake.** Free Community Cloud apps sleep after a while without visitors. The workflow
`.github/workflows/keep-alive.yml` runs every 4 hours, opens the app in a headless browser
(`scripts/keep_alive.py`) and clicks "Yes, get this app back up!" if it was asleep. It reads the app address from the
repository variable `APP_URL` (Settings → Secrets and variables → Actions → Variables). Run it by hand from the
Actions tab to test; the log ends with "App is up."
