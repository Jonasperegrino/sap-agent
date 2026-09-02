"""Atlas for SAP — Streamlit UI."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from sap_agent.schemas import Config
from sap_agent.ui.service import RunResult, run_question

# Streamlit Cloud: inject secrets into env so Config.from_env picks them up
try:
    import os as _os2

    for _k in ("SAP_AGENT_URL", "SAP_AGENT_USER", "SAP_AGENT_PASSWORD", "SAP_AGENT_LLM_API_KEY"):
        if _k in st.secrets and _k not in _os2.environ:
            _os2.environ[_k] = str(st.secrets[_k])
except Exception:
    pass

# Ensure Chromium is installed for Playwright (Streamlit Cloud post-install)
try:
    import os as _os3
    import pathlib as _pl

    import playwright as _pw

    _chromium_marker = _pl.Path(_pw.__file__).parent / "driver" / "package" / "lib" / "server" / "chromium"
    if not list(_chromium_marker.glob("*")):
        import subprocess as _sp

        _sp.run(["playwright", "install", "chromium"], check=False, timeout=120)
        _os3.environ["PLAYWRIGHT_BROWSERS_PATH"] = "0"
except Exception:
    pass

st.set_page_config(page_title="Atlas for SAP", page_icon="🌍", layout="wide")

st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
    * { font-family: 'Inter', system-ui, -apple-system, sans-serif; }
    [data-testid="stHeader"] { background: transparent !important; }
    header[data-testid="stHeader"] { background: transparent !important; }
    [data-testid="stAppViewContainer"] { background: #0a0f1a; color: #e2e8f0; }
    [data-testid="stAppViewBlockContainer"] { background: transparent; }
    [data-testid="stSidebar"] {
        background: #0f172a; border-right: 1px solid rgba(0,212,255,0.12);
    }
    [data-testid="stSidebar"] label,
    [data-testid="stSidebar"] p,
    [data-testid="stSidebar"] span { color: #e2e8f0 !important; }
    [data-testid="stSidebar"] .stCaption { color: #94a3b8 !important; }
    [data-testid="stAppDeployButton"] { display: none !important; }
    .atlas-title {
        font-size: 2.8rem; font-weight: 800; text-align: center;
        background: linear-gradient(135deg, #00d4ff 0%, #00ff88 100%);
        -webkit-background-clip: text; -webkit-text-fill-color: transparent;
        background-clip: text;
    }
    .atlas-sub { color: #94a3b8; text-align: center; margin-bottom: 1.5rem; }
    .glass-card {
        background: rgba(15,23,42,0.70); backdrop-filter: blur(10px);
        border: 1px solid rgba(0,212,255,0.18); border-radius: 12px;
        padding: 1.2rem; margin-bottom: 1rem;
    }
    .glass-card-success {
        background: rgba(0,212,255,0.10); border-color: rgba(0,212,255,0.30);
    }
    .glass-card-error {
        background: rgba(239,68,68,0.10); border-color: rgba(239,68,68,0.30);
    }
    .stTabs [data-baseweb="tab-list"] {
        gap: 0.5rem; background: rgba(15,23,42,0.55); padding: 0.4rem;
        border-radius: 10px; border: 1px solid rgba(0,212,255,0.12);
    }
    .stTabs [data-baseweb="tab"] {
        background: transparent; border-radius: 8px;
        padding: 0.5rem 1.2rem; color: #94a3b8; font-weight: 500;
    }
    .stTabs [aria-selected="true"] {
        background: rgba(0,212,255,0.18) !important; color: #00d4ff !important;
        border: 1px solid rgba(0,212,255,0.25);
    }
    .stTabs [data-baseweb="tab-border"],
    .stTabs [data-baseweb="tab-highlight"] { display: none; }
    div[data-testid="stButton"] button[kind="primary"],
    div[data-testid="stFormSubmitButton"] button[kind="primary"] {
        background: linear-gradient(135deg, #00d4ff 0%, #0099cc 100%) !important;
        color: #0a0f1a !important; border: none !important;
        border-radius: 8px !important; font-weight: 700 !important;
    }
    div[data-testid="stButton"] button:hover {
        background: linear-gradient(135deg, #00ff88 0%, #00d4ff 100%) !important;
        box-shadow: 0 0 18px rgba(0,212,255,0.35) !important;
    }
    [data-testid="stSidebar"] .stTextInput input,
    div[data-testid="stTextArea"] textarea {
        background: #1e293b !important; color: #f1f5f9 !important;
        border: 1px solid rgba(0,212,255,0.22) !important; border-radius: 8px !important;
    }
    [data-testid="stSidebar"] .stTextInput input:focus,
    div[data-testid="stTextArea"] textarea:focus {
        border-color: #00d4ff !important; box-shadow: 0 0 0 1px rgba(0,212,255,0.28) !important;
    }
    div[data-testid="stTextArea"] textarea::placeholder { color: #64748b !important; }
    [data-testid="stSidebar"] .stSelectbox > div > div {
        background: #1e293b !important; border: 1px solid rgba(0,212,255,0.22) !important;
        color: #f1f5f9 !important;
    }
    [data-testid="stMetric"] {
        background: rgba(15,23,42,0.55); border: 1px solid rgba(0,212,255,0.14);
        border-radius: 10px; padding: 0.8rem;
    }
    [data-testid="stMetricValue"] { color: #00d4ff; font-weight: 700; }
    [data-testid="stMetricLabel"] {
        color: #94a3b8; text-transform: uppercase; letter-spacing: 0.05em; font-size: 0.75rem;
    }
    .stSpinner > div { border-top-color: #00d4ff !important; }
    footer {
        margin-top: 2.5rem; padding: 1.2rem 0;
        border-top: 1px solid rgba(0,212,255,0.12);
        text-align: center; color: #64748b; font-size: 0.8rem;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# World map — fixed img behind, small JPEG (42KB)
try:
    import base64

    _p = Path(__file__).parent / "assets" / "worldmap_small.jpg"
    if _p.exists():
        _b64 = base64.b64encode(_p.read_bytes()).decode()
        st.markdown(
            f'<img src="data:image/jpeg;base64,{_b64}" '
            'style="position:fixed;inset:0;width:100%;height:100%;'
            'object-fit:cover;opacity:0.13;z-index:0;pointer-events:none;" />',
            unsafe_allow_html=True,
        )
        st.markdown(
            """<style>
            [data-testid="stApp"],[data-testid="stAppViewContainer"]{background:transparent !important;}
            [data-testid="stMain"],[data-testid="stSidebar"]{position:relative;z-index:1;}
            </style>""",
            unsafe_allow_html=True,
        )
except Exception:
    pass

st.markdown('<div class="atlas-title">Atlas for SAP</div>', unsafe_allow_html=True)
st.markdown('<div class="atlas-sub">Autonomous SAP Fiori discovery & Q&A agent</div>', unsafe_allow_html=True)

if "last_result" not in st.session_state:
    st.session_state["last_result"] = None

with st.sidebar:
    st.header("Connection")
    import os as _os

    _default_url = _os.environ.get("SAP_AGENT_URL", "https://jonasperegrino.github.io/sap-fiori/")
    app_url = st.text_input("App URL", value=_default_url)
    username = st.text_input("Username", value="demo")
    password = st.text_input("Password", type="password", value="password123")
    st.caption("Demo: demo / password123")
    route_label = st.selectbox("Answer against", ["Current page", "Dashboard", "Catalog", "Orders"])
    route = {"Current page": None, "Dashboard": "dashboard", "Catalog": "catalog", "Orders": "orders"}[route_label]
    st.caption("Credentials are used for this run only.")

tab_ask, tab_reports = st.tabs(["Ask", "Reports"])

with tab_ask:
    question = st.text_area("Question", placeholder="How many orders were placed in 2026?", height=110)
    ask = st.button("Ask the agent", type="primary", use_container_width=True, key="ask_btn")
    if ask:
        if not question.strip():
            st.warning("Enter a question before asking.")
        else:
            import urllib.error
            import urllib.request

            try:
                with urllib.request.urlopen(app_url, timeout=2) as r:
                    if r.status >= 400:
                        raise urllib.error.URLError(f"HTTP {r.status}")
            except Exception as e:  # noqa: BLE001
                st.error(f"App not reachable at {app_url}: {e}")
            else:
                cfg = Config.from_env(app_url=app_url, username=username, password=password)
                cfg.login_timeout_ms = 8000
                cfg.retry_budget = 1
                try:
                    with st.spinner("Logging in and inspecting the app…"):
                        res = run_question(cfg, question.strip(), route)
                    st.session_state["last_result"] = res
                except Exception as e:  # noqa: BLE001
                    st.error(f"Agent crashed: {e}")

    res: RunResult | None = st.session_state.get("last_result")
    if res and res.answer:
        a = res.answer
        if a.unsupported:
            st.warning(a.message or "Question not supported.")
        elif a.not_found:
            st.info(a.message or "No matching rows found.")
        else:
            st.success(f"Answer: {a.answer}")
        c1, c2, c3 = st.columns(3)
        c1.metric("Confidence", a.confidence)
        c2.metric("Matched rows", a.evidence.matched_rows)
        c3.metric("Intent", a.intent.value)
        with st.expander("Evidence and trace"):
            st.json({"evidence": a.evidence.model_dump(), "checksum": a.checksum, "trace": res.trace})
        if st.button("Clear result", key="clear_ask"):
            st.session_state["last_result"] = None
            st.rerun()
    elif res and res.report:
        if res.report.classification.value == "unsupported_auth_flow" or "Invalid credentials" in (
            res.error or res.report.actual or ""
        ):
            st.error("Login failed — invalid credentials")
            st.info("Demo app expects **demo / password123**. Check the sidebar and try again.")
            with st.expander("Details"):
                st.write(res.error or res.report.actual)
                st.caption(f"Classification: `{res.report.classification.value}`")
        else:
            st.error(f"Run failed: {res.report.classification.value}")
            st.write(res.error or res.report.actual)
        if res.report_path and res.report_path.exists():
            st.download_button("Download bug report", res.report_path.read_bytes(), file_name=res.report_path.name)
        if st.button("Clear error", key="clear_err"):
            st.session_state["last_result"] = None
            st.rerun()

with tab_reports:
    st.subheader("Generated reports")
    adir = Path("artifacts")
    reps = sorted(adir.glob("bug_report.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not reps:
        st.info("No automatic bug reports have been generated.")
    else:
        rp = reps[0]
        st.caption(str(rp))
        st.markdown(rp.read_text())
        st.download_button("Download latest report", rp.read_bytes(), file_name=rp.name)
