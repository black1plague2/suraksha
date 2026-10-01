"""Smoke test: every persona page of the Streamlit app renders without exception."""
from pathlib import Path

import pytest

pytest.importorskip("streamlit")
pytest.importorskip("pandas")
from streamlit.testing.v1 import AppTest  # noqa: E402

APP = str(Path(__file__).resolve().parents[2] / "app" / "streamlit_app.py")
PAGES = ["Trade-ops analyst", "Investigator", "Compliance officer (MLRO)", "Risk head"]


@pytest.mark.parametrize("page", PAGES)
def test_page_renders(page):
    at = AppTest.from_file(APP, default_timeout=120).run()
    assert not at.exception, at.exception
    at.sidebar.radio(key="persona").set_value(page).run()
    assert not at.exception, at.exception


def test_investigator_question():
    at = AppTest.from_file(APP, default_timeout=120).run()
    at.sidebar.radio(key="persona").set_value("Investigator").run()
    at.text_input(key="inv_q").set_value("directors of C0001").run()
    assert not at.exception, at.exception


def test_mlro_has_verify_button():
    at = AppTest.from_file(APP, default_timeout=120).run()
    at.sidebar.radio(key="persona").set_value("Compliance officer (MLRO)").run()
    assert not at.exception, at.exception
    assert any("Verify" in b.label for b in at.button)
