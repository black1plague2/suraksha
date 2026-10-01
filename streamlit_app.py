"""Root entrypoint shim for Streamlit in Snowflake (warehouse runtime needs a bare MAIN_FILE).

Adds ./src to sys.path and runs app/streamlit_app.py unchanged.
Local use is unaffected: `streamlit run app/streamlit_app.py` still works.
"""
import runpy
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))
runpy.run_path(str(_ROOT / "app" / "streamlit_app.py"), run_name="__main__")
