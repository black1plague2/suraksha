"""Static checks on sql/*.sql (no Snowflake needed)."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SQL_DIR = ROOT / "sql"
SQL_FILES = sorted(SQL_DIR.glob("*.sql"))
REPO_URL = "https://github.com/black1plague2/suraksha.git"


def split_statements(text: str) -> list[str]:
    """Split on ';' outside '...' strings, $$ blocks and -- / /* */ comments (comments dropped)."""
    out: list[str] = []
    buf: list[str] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if text.startswith("--", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
        elif text.startswith("$$", i):
            j = text.find("$$", i + 2)
            assert j >= 0, "unterminated $$ block"
            buf.append(text[i:j + 2])
            i = j + 2
        elif c == "'":
            j = i + 1
            while j < n:
                if text[j] == "'" and text[j + 1:j + 2] == "'":
                    j += 2
                elif text[j] == "'":
                    break
                else:
                    j += 1
            assert j < n, "unterminated string literal"
            buf.append(text[i:j + 1])
            i = j + 1
        elif c == ";":
            stmt = "".join(buf).strip()
            if stmt:
                out.append(stmt)
            buf = []
            i += 1
        else:
            buf.append(c)
            i += 1
    tail = "".join(buf).strip()
    if tail:
        out.append(tail)
    return out


def test_expected_files_present():
    names = {p.name for p in SQL_FILES}
    for n in ("06_git_repo.sql", "07_load_synth_proc.sql", "08_run_pipeline_proc.sql", "09_streamlit.sql"):
        assert n in names


@pytest.mark.parametrize("path", SQL_FILES, ids=lambda p: p.name)
def test_file_parses_into_statements(path):
    stmts = split_statements(path.read_text(encoding="utf-8"))
    assert stmts, "no statements"
    for s in stmts:
        assert re.match(r"^[A-Za-z(]", s), f"odd statement start: {s[:40]!r}"


@pytest.mark.parametrize("path", SQL_FILES, ids=lambda p: p.name)
def test_no_hardcoded_secrets(path):
    text = path.read_text(encoding="utf-8")
    assert not re.search(r"ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|gho_[A-Za-z0-9]{20,}", text)
    assert not re.search(r"(?i)\b(password|passwd|token|secret|api_key)\s*=\s*'[^']+'", text)
    assert not re.search(r"-----BEGIN [A-Z ]*PRIVATE KEY", text)
    assert not re.search(r"https://[^/\s:@']+:[^/\s@']+@", text), "credentials embedded in a URL"


def test_06_repo_definition():
    text = (SQL_DIR / "06_git_repo.sql").read_text(encoding="utf-8")
    assert f"ORIGIN = '{REPO_URL}'" in text
    assert "CREATE OR REPLACE GIT REPOSITORY SURAKSHA.CORE.SURAKSHA_REPO" in text
    assert "API_INTEGRATION = github_api" in text
    assert "GIT_CREDENTIALS = github_pat" in text
    assert "FETCH" in text
    # database/schema bootstrap must precede the repo object
    assert text.index("CREATE DATABASE IF NOT EXISTS SURAKSHA") < text.index("CREATE OR REPLACE GIT REPOSITORY")


def test_06_deploy_order_and_files_exist():
    raw = (SQL_DIR / "06_git_repo.sql").read_text(encoding="utf-8")
    text = chr(10).join(l for l in raw.splitlines() if not l.lstrip().startswith("--"))
    refs = re.findall(r"EXECUTE IMMEDIATE FROM @SURAKSHA\.CORE\.SURAKSHA_REPO/branches/main/(sql/[\w.]+\.sql)", text)
    assert [Path(r).name[:2] for r in refs] == ["00", "01", "02", "03", "04", "05", "07", "08", "09"]
    for r in refs:
        assert (ROOT / r).is_file(), r


STAGE_REF = re.compile(r"@SURAKSHA\.CORE\.SURAKSHA_REPO/branches/main/([\w./-]*)")


@pytest.mark.parametrize("name", ["07_load_synth_proc.sql", "08_run_pipeline_proc.sql", "09_streamlit.sql"])
def test_repo_paths_exist(name):
    text = (SQL_DIR / name).read_text(encoding="utf-8")
    code = "\n".join(l for l in text.splitlines() if not l.strip().startswith("--"))
    for rel in STAGE_REF.findall(code):
        if not rel or rel.endswith("/"):
            assert (ROOT / rel).is_dir() if rel else True, rel
        else:
            assert (ROOT / rel).exists(), rel


def test_09_streamlit_main_file_and_env():
    text = (SQL_DIR / "09_streamlit.sql").read_text(encoding="utf-8")
    m = re.search(r"MAIN_FILE\s*=\s*'([^']+)'", text)
    assert m and (ROOT / m.group(1)).is_file()
    assert "/" not in m.group(1), "warehouse runtime needs a bare filename"
    assert "QUERY_WAREHOUSE" in text
    assert (ROOT / "environment.yml").is_file()
    shim = (ROOT / "streamlit_app.py").read_text(encoding="utf-8")
    assert "app" in shim and "src" in shim


@pytest.mark.parametrize("name", ["07_load_synth_proc.sql", "08_run_pipeline_proc.sql"])
def test_proc_python_bodies_compile_and_import_real_modules(name):
    text = (SQL_DIR / name).read_text(encoding="utf-8")
    bodies = re.findall(r"\$\$(.*?)\$\$", text, re.S)
    assert len(bodies) == 1
    compile(bodies[0], name, "exec")
    for mod in re.findall(r"from (suraksha[\w.]*) import", bodies[0]):
        rel = Path("src", *mod.split("."))
        assert (ROOT / rel).with_suffix(".py").is_file() or (ROOT / rel / "__init__.py").is_file(), mod
    assert re.search(r"HANDLER = 'run'", text) and "def run(session, seed)" in bodies[0]
