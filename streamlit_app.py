"""Project-root entry point for the Streamlit operator UI."""

from pathlib import Path

exec((Path(__file__).parent / "sap_agent" / "ui" / "streamlit_app.py").read_text())
