# Suraksha

Catches trade-finance **duplicate financing** before the money leaves the bank: flags cargo pledged to more than
one lender, proves the link between "unrelated" borrowers, and drafts an audit-ready, fully-cited STR for a named
compliance officer to approve.

Snowflake CoCo CLI Hackathon (GCC Edition) · Track: Risk, Fraud and Regulatory Intelligence Copilot.

## Quickstart (local, no credentials needed)
```bash
pip install -e ".[dev]"
python -m pytest -q
python scripts/eval.py
```

## Docs
- [PRD](docs/PRD.md) · [Architecture](docs/ARCHITECTURE.md) · [Contracts](docs/CONTRACTS.md)
- [Runbook](docs/RUNBOOK.md) · [Snowflake deploy](docs/SNOWFLAKE_DEPLOY.md) · [CoCo usage](docs/COCO_USAGE.md)
- [Testing](docs/TESTING.md) · [Handoff log](docs/HANDOFF_LOG.md) · [Iteration logs](logs/iterations/)

All data is synthetic.
