# Suraksha — Module Contracts & Ownership

Single source of truth for parallel agents. **Master (Opus)** owns `models.py`, `store/base.py`,
`store/memory.py`, `config.py`, `log.py`, `pipeline.py`, all of `docs/` except an agent's own log,
and `tests/e2e/`. Each agent owns ONLY the files listed for it. Never edit another agent's files.
Contract change needed? Write it under "Contract change requests" in `docs/HANDOFF_LOG.md`.

Rules for every agent:
- Python 3.11+, standard library only in `src/suraksha` core (no pip installs except pytest). Snowflake/Streamlit code may import their libs lazily.
- Log through `suraksha.log.get_logger(__name__)` with `extra={"ctx": {...}}`. Log every decision point.
- Write unit tests in `tests/unit/test_<module>.py`; run `python -m pytest tests/unit -q` from repo root before finishing.
- Write your agent log to `docs/agent-logs/<agent-name>.md`: what you built, decisions, test results (paste the pytest summary line), known gaps.
- Do NOT git commit. Master integrates and commits.

---

## A. `synth` — agent **synth-data** (Sonnet)
Files: `src/suraksha/synth/__init__.py`, `src/suraksha/synth/generator.py`, `src/suraksha/synth/templates.py`, `tests/unit/test_synth.py`

```python
@dataclass
class SynthDataset:
    companies, persons, roles, corp_owners, addresses, transactions, policy_clauses: list[dict]   # shapes in store/base.py
    requests: list[FinancingRequest]          # sorted by submitted_at ascending (process in this order)
    labels: dict[str, bool]                   # request_id -> True if this request MUST be flagged (non-CLEAR)
    scenarios: dict[str, str]                 # request_id -> scenario name (see below)
    borrower_reg_no: dict[str, str]           # company_id -> reg_no (convenience)

def generate(seed: int = 42, n_clean: int = 60, n_dup: int = 30, n_decoy: int = 20) -> SynthDataset
def load_into(store, ds: SynthDataset) -> None   # calls store.load_registry(...)
```
Deterministic for a given seed (use `random.Random(seed)`, fixed base datetime 2026-06-01).
Three banks: BANK_A, BANK_B, BANK_C. Currencies INR/USD. Commodities: steel coils, copper cathodes,
aluminium ingots, crude palm oil, nickel, sugar, urea, etc. Indian + GCC ports (Mundra, JNPT, Chennai, Jebel Ali, Dammam, Qingdao, Singapore).

Scenarios (each duplicate case = an ORIGINAL request (label False, scenario `dup_original_*`) at bank X followed by 1–2 DUPLICATE requests (label True) at a different bank within 1–40 days):
- `dup_exact_same_borrower`   — identical docs, same borrower company at both banks
- `dup_exact_shell`           — identical docs; second borrower is a different-named shell sharing a UBO/director/address/phone with the first (via 1–3 hops, sometimes through a holding company in `corp_owners`)
- `dup_reissued_bl`           — new B/L number, same vessel/voyage/commodity/qty; borrower linked as in shell
- `dup_rounded_qty`           — same B/L, quantity rounded (e.g. 4,987.5 MT → 5,000 MT); borrower linked
- `dup_format_noise`          — same cargo but text noise: case changes, "M/V"/"MV" prefix, extra spaces, commas in numbers, DD/MM/YYYY dates, qty in KG
- `dup_weak_unlinked`         — fuzzy (reissued B/L) with NO registry link to the original borrower (still label True: should land in NEED_MORE_EVIDENCE, not CLEAR)
Decoys (label False, must end CLEAR):
- `decoy_coloaded`            — same vessel+voyage, different commodity, different B/L, at another bank
- `decoy_related_diff_cargo`  — borrower shares director with another borrower but different cargo
- `decoy_same_commodity_diff_voyage` — same commodity & qty, different vessel/voyage
- `decoy_same_bank_refinance` — same cargo re-presented at the SAME bank (consortium excludes own bank)
- `decoy_hard_same_voyage_same_qty` — at most 2 cases: unrelated shippers, same vessel/voyage/commodity, qty in same band, different B/L. (Honest hard negatives; may become FPs.)
Clean: unique cargo, no relation.

Document text templates (Intake must parse these; noise variants allowed only in `dup_format_noise` and ~20% of others):
```
BILL OF LADING
B/L No: {bl}                      # alias "B/L Number", "Bill of Lading No."
Shipper: {shipper}
Consignee: {consignee}
Vessel: {vessel}                  # alias "Ocean Vessel"; value may carry "M/V " or "MV " prefix
Voyage No: {voyage}               # alias "Voy. No", "Voyage"
Port of Loading: {pol}
Port of Discharge: {pod}
Description of Goods: {commodity}
Quantity: {qty} {unit}            # unit MT | TONNES | KG; qty may contain commas
Shipped on Board: {date}          # YYYY-MM-DD or DD/MM/YYYY

COMMERCIAL INVOICE
Invoice No: {inv}
Seller: {shipper}
Buyer: {consignee}
Goods: {commodity}
Quantity: {qty} {unit}
Total Value: {currency} {value}   # value may contain commas
B/L Ref: {bl}

LETTER OF CREDIT
LC No: {lc}
Applicant: {consignee}
Beneficiary: {shipper}
Amount: {currency} {value}
Goods: {commodity}
Port of Loading: {pol}
Port of Discharge: {pod}
Latest Shipment Date: {date}

WAREHOUSE RECEIPT
Receipt No: {wr}
Depositor: {borrower name}
Commodity: {commodity}
Quantity: {qty} {unit}
Ex Vessel: {vessel} / {voyage}
```
Policy clauses: ~12 synthetic clauses with ids like `TF-3.1`, `TF-4.2`, `AML-7.4`, tags such as `duplicate_financing`, `collateral`, `related_party`, `str_filing`, `hold`.

**Citation conventions (ITER-01 resolution):** request-level facts use `Citation(REGISTRY, "requests:<request_id>")`;
persons `persons:<id>`; addresses `addresses:<id>`. Fingerprints may carry extra private keys (`cargo_m1`, `cargo_p1`
neighbour-band probes) — `to_entry` strips them so the shared ledger holds only `exact`/`cargo`/`bl`/`blv`.
`blv` (ITER-02) = H(bl_norm | voyage_norm | commodity) — vessel-independent; catches vessel typos/renames. Voyage normalisation strips V./VOY prefixes and leading zeros ("066S" = "V.066S" = "66S").

## B. `intake` + `fingerprint` — agent **match-engine** (Sonnet)
Files: `src/suraksha/agents/intake.py`, `src/suraksha/agents/fingerprint.py`, `tests/unit/test_intake.py`, `tests/unit/test_fingerprint.py`
```python
# intake.py
def extract(req: FinancingRequest) -> ExtractedFields     # deterministic regex parser over doc text; every filled field gets a sources[...] DOCUMENT citation (doc_id, page, snippet)
def normalize_quantity(qty: float, unit: str) -> tuple[float, str]   # -> MT
# fingerprint.py
def normalize(fields: ExtractedFields) -> dict[str, str]  # bl_norm (alnum upper), vessel (strip M/V, MV, upper, collapse spaces), voyage, commodity (canonical), qty_band
def borrower_token(reg_no: str, salt: str) -> str          # sha256 hex
def build_fingerprint(req: FinancingRequest, fields: ExtractedFields, borrower_reg_no: str, settings: Settings) -> Fingerprint
def to_entry(fp: Fingerprint, entry_id: str | None = None) -> ConsortiumEntry   # pledged_at = req.submitted_at (= fp.created_at)
def match(fp: Fingerprint, store: Store) -> list[ConsortiumMatch]
    # EXACT if keys["exact"] equal; FUZZY via "cargo" (same band OR neighbour bands ±1 using find_consortium_by_band) or "bl".
    # similarity: EXACT 1.0; cargo same band 0.9; bl 0.85; neighbour band 0.75. One match per entry (best). Sorted desc.
    # citation: Citation(CONSORTIUM, entry.entry_id, snippet="matched keys: ...")
```
Hash = sha256(salt + "|" + "|".join(parts)). Commodity canonicalisation map (e.g. "HR STEEL COILS"/"hot rolled steel coils" -> "STEEL_COILS_HR").

## C. `investigator` + `confidence` — agent **investigator** (Sonnet)
Files: `src/suraksha/agents/investigator.py`, `src/suraksha/agents/confidence.py`, `src/suraksha/agents/linkage.py`, `tests/unit/test_investigator.py`, `tests/unit/test_confidence.py`
```python
# investigator.py
def resolve_borrower(token: str, store: Store, salt: str) -> str | None     # hash every company reg_no, return company_id
def investigate(req: FinancingRequest, match: ConsortiumMatch, store: Store, settings: Settings) -> Investigation
    # graph nodes: company:<id>, person:<id>, address:<id>, phone:<num>; edges from roles, corp_owners, companies.address_id/phone
    # BFS shortest paths (<= settings.max_graph_hops) between borrower and counterparty; every edge has a REGISTRY citation
    # shared_directors / shared_ubos / shared_addresses / shared_phones computed directly (also via one holding-company hop)
    # transactions = store.transactions_for(borrower, req.bank_id); policy = store.policy_clauses(["duplicate_financing","related_party","hold","str_filing"])
    # timing_overlap_days = abs(req.submitted_at - match.entry.pledged_at).days
# linkage.py  (investigator Q&A: "who else is linked to Company B?")
def linked_entities(company_id: str, store: Store, hops: int = 2) -> list[dict]   # [{company_id, name, via: [GraphEdge...]}]
def answer(question: str, store: Store) -> dict   # rule-based NL: finds company by name/id in question; intents: linked / path between X and Y / directors of X. Returns {"intent","answer_text","rows","citations"}
# confidence.py
def score(inv: Investigation, settings: Settings) -> ConfidenceResult
    # deterministic rules with RULE_WEIGHTS from config; each fired rule -> Evidence with citations
    # same borrower at both banks counts as R_SHARED_UBO + R_SHARED_DIRECTOR (identity is the strongest link)
    # band HIGH if score >= settings.confidence_threshold else LOW; LOW -> `missing` lists concrete asks
```

## D. `report` + `approval` + `slack` — agent **report-approval** (Sonnet)
Files: `src/suraksha/agents/report.py`, `src/suraksha/agents/approval.py`, `src/suraksha/integrations/slack.py`, `tests/unit/test_report.py`, `tests/unit/test_approval.py`, `tests/unit/test_slack.py`
```python
# report.py — FIU-IND STR structure
def draft_str(req, fields, inv, conf, settings) -> STRDraft
    # sections: PART 1 Reporting entity; PART 2 Principal officer; PART 3 Transaction details;
    # PART 4 Linked individuals/entities; PART 5 Grounds of suspicion (narrative); PART 6 Action taken / recommended
    # EVERY ReportSentence has >=1 Citation (DOCUMENT/REGISTRY/CONSORTIUM/POLICY/RULE/TRANSACTION)
    # recommended_action = "HOLD_DISBURSEMENT"
def validate_citations(draft: STRDraft) -> list[str]     # error strings; [] = valid
def render_markdown(draft: STRDraft) -> str               # citations as [^n] footnotes
# approval.py
class AuditLog:
    def __init__(self, store): ...
    def append(self, actor: str, action: str, subject_id: str, payload: dict) -> AuditRecord   # sha256 hash chain, genesis prev_hash "0"*64
    def verify(self) -> bool
def open_case(report: STRDraft, store, audit: AuditLog) -> Case        # PENDING_APPROVAL, hold_recommended False
def decide(case_id: str, decision: Decision, officer: str, reason: str, store, audit: AuditLog) -> Case
    # officer must be non-empty named human (not "system:*") else ValueError; already-decided -> ValueError
    # APPROVE -> FILED, hold_recommended True; REJECT -> CLOSED (reason required)
# integrations/slack.py
def build_approval_message(case: Case, report: STRDraft) -> dict      # Block Kit with Approve / Reject buttons, action_id "suraksha_approve"/"suraksha_reject", value=case_id
def post_for_approval(case, report, webhook_url: str | None) -> bool  # urllib POST; returns False (logs) if no URL
def handle_action(payload: dict, store, audit) -> Case                # maps Slack interactive payload (user.name) -> decide(...)
```

## E. Snowflake deploy layer — agent **snowflake-infra** (Sonnet)
Files: `sql/*.sql`, `src/suraksha/store/snowflake.py`, `src/suraksha/integrations/cortex.py`, `scripts/load_synth_to_snowflake.py`, `tests/unit/test_snowflake_store.py` (mock connector), `docs/SNOWFLAKE_DEPLOY.md`
- `sql/00_setup.sql` warehouse/db/schemas (SURAKSHA.CORE, SURAKSHA.REGISTRY, SURAKSHA.CONSORTIUM, SURAKSHA.BANK_A/B/C), roles
- `sql/01_tables.sql` all tables mirroring store/base.py shapes (+ VARIANT for keys/payload)
- `sql/02_consortium_share.sql` secure view exposing only hashes; `CREATE SHARE` for consortium; per-bank write via stored proc
- `sql/03_audit_immutability.sql` audit table, insert-only role grants, hash-chain verify view
- `sql/04_rules.sql` deterministic confidence rules as SQL views (mirror config.RULE_WEIGHTS)
- `sql/05_cortex.sql` stage for PDFs, AI_EXTRACT / AI_COMPLETE usage for intake + STR narrative
- `store/snowflake.py`: `SnowflakeStore` implementing Store via snowflake-connector (lazy import), config from env `SNOWFLAKE_*` or `~/.snowflake/connections.toml`
- `integrations/cortex.py`: `cortex_extract(stage_path) -> ExtractedFields`, `cortex_complete(prompt) -> str` (lazy, raise clear error when not configured)

## F. Docs & CoCo usage — agent **docs-coco** (Haiku)
Files: `docs/COCO_USAGE.md`, `docs/RUNBOOK.md`, `docs/GLOSSARY.md`, `docs/agent-logs/docs-coco.md`
- COCO_USAGE.md: concrete Snowflake Cortex Code (CoCo) CLI prompts for each phase PLAN / BUILD / RUN / TEST mapped to this repo's files, with a table "phase → prompt → artifact produced → evidence to screenshot for judges".
- RUNBOOK.md: local quickstart (uv/pip, pytest, demo), Snowflake deploy steps (reference sql/ order), Slack setup, troubleshooting.
- GLOSSARY.md: LC, B/L, WR, UBO, STR, FIU-IND, MLRO, consortium, fingerprint, etc.
