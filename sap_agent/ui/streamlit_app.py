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
    import pathlib as _pl2
    import subprocess as _sp2

    _found = False
    for _base in [
        _pl2.Path.home() / ".cache" / "ms-playwright",
        _pl2.Path.home() / "Library" / "Caches" / "ms-playwright",
        _pl2.Path("/home/appuser/.cache/ms-playwright"),
    ]:
        if _base.exists() and any(_base.glob("chromium*")):
            _found = True
            break
    if not _found:
        # install both chromium and headless shell; --with-deps fails without sudo on Cloud,
        # so try without, then fallback
        for _cmd in (
            ["playwright", "install", "chromium"],
            ["playwright", "install", "chromium-headless-shell"],
            ["python", "-m", "playwright", "install", "chromium"],
        ):
            try:
                _sp2.run(_cmd, check=False, timeout=180)
                # re-check
                for _base in [
                    _pl2.Path.home() / ".cache" / "ms-playwright",
                    _pl2.Path("/home/appuser/.cache/ms-playwright"),
                ]:
                    if _base.exists() and any(_base.glob("chromium*")):
                        _found = True
                        break
                if _found:
                    break
            except Exception:
                continue
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
    st.caption("Credentials are used for this run only. Agent auto-finds the right page.")

    st.divider()
    st.subheader("AI / LLM (optional)")
    # Prefill from env/secrets if present, else empty
    _env_key = _os.environ.get("SAP_AGENT_LLM_API_KEY", "") or _os.environ.get("OPENAI_API_KEY", "")
    llm_default_model = _os.environ.get("SAP_AGENT_LLM_MODEL", "gpt-5")
    llm_api_key_input = st.text_input(
        "OpenAI API Key",
        type="password",
        value="",
        placeholder="sk-..." if not _env_key else "using key from secrets",
        help="For aggregate questions (e.g. revenue of top 3 clients). Leave empty for deterministic mode. Not stored.",
    )
    llm_model_input = st.text_input(
        "Model",
        value=llm_default_model,
        placeholder="gpt-5",
        help="OpenAI model for intent parsing",
    )
    llm_base_url_input = st.text_input(
        "Base URL (optional)",
        value=_os.environ.get("SAP_AGENT_LLM_BASE_URL", ""),
        placeholder="https://api.openai.com/v1",
        help="Override for OpenAI-compatible endpoints",
    )
    if _env_key and not llm_api_key_input:
        st.caption("🔑 API key from secrets/env active")
    elif llm_api_key_input:
        st.caption(f"🔑 Using key …{llm_api_key_input[-4:]} for this run")
    else:
        st.caption("Deterministic mode — aggregate queries may be unsupported")

tab_ask, tab_reports = st.tabs(["Ask", "Reports"])

with tab_ask:
    question = st.text_area(
        "Question",
        placeholder="How many orders were placed in 2026?\nWith AI: revenue of top 3 clients last year",
        height=110,
    )
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
                # Inject sidebar LLM key if provided (overrides env/secrets)
                if llm_api_key_input.strip():
                    from pydantic import SecretStr

                    cfg.llm_api_key = SecretStr(llm_api_key_input.strip())
                    if llm_model_input.strip():
                        cfg.llm_model = llm_model_input.strip()
                    if llm_base_url_input.strip():
                        cfg.llm_base_url = llm_base_url_input.strip().rstrip("/")
                elif llm_model_input.strip() != cfg.llm_model:
                    cfg.llm_model = llm_model_input.strip()
                if llm_base_url_input.strip():
                    cfg.llm_base_url = llm_base_url_input.strip().rstrip("/")
                try:
                    with st.spinner("Logging in and inspecting the app…"):
                        res = run_question(cfg, question.strip(), None)
                    st.session_state["last_result"] = res
                except Exception as e:  # noqa: BLE001
                    st.error(f"Agent crashed: {e}")

    res: RunResult | None = st.session_state.get("last_result")
    if res and res.answer:
        a = res.answer
        _llm_used = any("llm" in str(t.get("tool", "")) + str(t.get("action", "")) for t in (res.trace or []))
        if a.unsupported:
            st.warning(a.message or "Question not supported.")
            if not _llm_used and not _env_key and not llm_api_key_input.strip():
                st.info(
                    "💡 Tip: aggregate questions (e.g. revenue of top 3 clients) "
                    "need an OpenAI API key — add it in the sidebar under AI / LLM."
                )
        elif a.not_found:
            st.info(a.message or "No matching rows found.")
        else:
            # nice rendering for customer lookup
            if a.intent.value == "lookup" and isinstance(a.answer, list) and a.answer:
                rec = a.answer[0]
                st.success(
                    f"Contact for {rec.get('customer', '')}: "
                    f"**{rec.get('contact', '')}** — {rec.get('contactTitle', '')}"  # noqa: E501
                )
                c1, c2 = st.columns(2)
                c1.markdown(f"**Email:** {rec.get('email', '')}")
                c2.markdown(f"**Phone:** {rec.get('phone', '')}")
                st.caption(f"{rec.get('city', '')}, {rec.get('country', '')} · {rec.get('industry', '')}")
            else:
                st.success(f"Answer: {a.answer}")
            if _llm_used:
                st.caption("🧠 AI-parsed intent")
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
