"""Atlas for SAP — Streamlit UI."""

from __future__ import annotations

import contextlib
import logging
from pathlib import Path

import streamlit as st

from sap_agent.schemas import Config
from sap_agent.ui.service import RunResult, run_question

# Streamlit Cloud captures stdout/stderr into the app logs — plain
# basicConfig is the whole integration: every module logger flows there.
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("fiori-agent")

# Streamlit Cloud: inject secrets into env so Config.from_env picks them up
try:
    import os as _os2

    for _k in ("SAP_AGENT_URL", "SAP_AGENT_USER", "SAP_AGENT_PASSWORD", "SAP_AGENT_LLM_API_KEY"):
        if _k in st.secrets and _k not in _os2.environ:
            _os2.environ[_k] = str(st.secrets[_k])
except (RuntimeError, OSError, AttributeError, ValueError, TypeError, KeyError) as exc:
    logger.debug("secret injection skipped: %s", exc)


# Ensure Chromium is installed for Playwright (Streamlit Cloud post-install).
# Cached: without this every keystroke-rerun re-globs the filesystem and can
# spawn 180s install probes at import time.
@st.cache_resource(show_spinner=False)
def _chromium_ready() -> bool:
    import pathlib as _pl2
    import subprocess as _sp2

    bases = [
        _pl2.Path.home() / ".cache" / "ms-playwright",
        _pl2.Path.home() / "Library" / "Caches" / "ms-playwright",
        _pl2.Path("/home/appuser/.cache/ms-playwright"),
    ]
    if any(_b.exists() and any(_b.glob("chromium*")) for _b in bases):
        return True
    # install both chromium and headless shell; --with-deps fails without sudo on Cloud,
    # so try without, then fallback
    for _cmd in (
        ["playwright", "install", "chromium"],
        ["playwright", "install", "chromium-headless-shell"],
        ["python", "-m", "playwright", "install", "chromium"],
    ):
        try:
            _sp2.run(_cmd, check=False, timeout=180)
            if any(_b.exists() and any(_b.glob("chromium*")) for _b in bases):
                return True
        except (OSError, _sp2.SubprocessError):
            continue
    return False


with contextlib.suppress(RuntimeError, OSError, AttributeError):
    _chromium_ready()

st.set_page_config(page_title="Atlas for SAP", page_icon="🌍", layout="wide")


# Load background image as base64 for CSS (cached: no re-encode per rerun).
@st.cache_resource(show_spinner=False)
def _load_bg() -> str:
    for _bp in [
        Path(__file__).parent / "assets" / "worldmap_small.jpg",
        Path(__file__).parent.parent / "sap_agent" / "ui" / "assets" / "worldmap_small.jpg",
        Path.cwd() / "sap_agent" / "ui" / "assets" / "worldmap_small.jpg",
        Path(__file__).resolve().parent / "assets" / "worldmap_small.jpg",
    ]:
        if _bp.exists():
            try:
                import base64

                return base64.b64encode(_bp.read_bytes()).decode()
            except (OSError, ValueError):
                continue
    return ""


_bg_img = _load_bg()
_bg_css = f"data:image/jpeg;base64,{_bg_img}" if _bg_img else "none"


@st.cache_data(show_spinner=False)
def _make_css(bg_css: str) -> str:
    return (
        "<style>"
        "@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');"
        "* { font-family: 'Inter', system-ui, -apple-system, sans-serif; }"
        '[data-testid="stHeader"] { background: transparent !important; }'
        'header[data-testid="stHeader"] { background: transparent !important; }'
        '[data-testid="stAppViewContainer"] { background-color: #0a0f1a;'
        " background-image: url(" + bg_css + ");"
        " background-size: cover; background-position: center;"
        " background-attachment: fixed; background-repeat: no-repeat;"
        " color: #e2e8f0; min-height: 100vh; }"
        " @media (prefers-reduced-motion: reduce) {"
        ' [data-testid="stAppViewContainer"] { background-attachment: scroll; } }'
        '[data-testid="stAppViewContainer"] > div:first-child {'
        " max-width: 1200px; margin: 0 auto; padding: 2rem 1.5rem; }"
        " @media (min-width: 768px) {"
        ' [data-testid="stAppViewContainer"] > div:first-child { padding: 3rem 2rem; } }'
        '[data-testid="stAppViewBlockContainer"] { background: transparent; }'
        '[data-testid="stSidebar"] { background: #0f172a; border-right: 1px solid rgba(0,212,255,0.12); }'
        '[data-testid="stSidebar"] label, [data-testid="stSidebar"] p, '
        '[data-testid="stSidebar"] span { color: #e2e8f0 !important; }'
        '[data-testid="stSidebar"] .stCaption { color: #94a3b8 !important; }'
        '[data-testid="stAppDeployButton"] { display: none !important; }'
        ".atlas-title { font-size: 2.8rem; font-weight: 800; text-align: center; "
        "background: linear-gradient(135deg, #00d4ff 0%, #00ff88 100%); "
        "-webkit-background-clip: text; -webkit-text-fill-color: transparent; background-clip: text; }"
        ".atlas-sub { color: #94a3b8; text-align: center; margin-bottom: 1.5rem; }"
        ".glass-card { background: rgba(15,23,42,0.70); backdrop-filter: blur(10px); "
        "border: 1px solid rgba(0,212,255,0.18); border-radius: 12px; "
        "padding: 1.2rem; margin-bottom: 1rem; }"
        ".glass-card-success { background: rgba(0,212,255,0.10); border-color: rgba(0,212,255,0.30); }"
        ".glass-card-error { background: rgba(239,68,68,0.10); border-color: rgba(239,68,68,0.30); }"
        '.stTabs [data-baseweb="tab-list"] { gap: 0.5rem; background: rgba(15,23,42,0.55); '
        "padding: 0.4rem; border-radius: 10px; border: 1px solid rgba(0,212,255,0.12); }"
        '.stTabs [data-baseweb="tab"] { background: transparent; border-radius: 8px; '
        "padding: 0.5rem 1.2rem; color: #94a3b8; font-weight: 500; }"
        '.stTabs [aria-selected="true"] { background: rgba(0,212,255,0.18) !important; '
        "color: #00d4ff !important; border: 1px solid rgba(0,212,255,0.25); }"
        '.stTabs [data-baseweb="tab-border"], .stTabs [data-baseweb="tab-highlight"] { display: none; }'
        'div[data-testid="stButton"] button[kind="primary"], '
        'div[data-testid="stFormSubmitButton"] button[kind="primary"] { '
        "background: linear-gradient(135deg, #00d4ff 0%, #0099cc 100%) !important; "
        "color: #0a0f1a !important; border: none !important; "
        "border-radius: 8px !important; font-weight: 700 !important; }"
        'div[data-testid="stButton"] button:hover { '
        "background: linear-gradient(135deg, #00ff88 0%, #00d4ff 100%) !important; "
        "box-shadow: 0 0 18px rgba(0,212,255,0.35) !important; }"
        '[data-testid="stSidebar"] .stTextInput input, '
        'div[data-testid="stTextArea"] textarea { '
        "background: #1e293b !important; color: #f1f5f9 !important; "
        "border: 1px solid rgba(0,212,255,0.22) !important; border-radius: 8px !important; }"
        '[data-testid="stSidebar"] .stTextInput input:focus, '
        'div[data-testid="stTextArea"] textarea:focus { '
        "border-color: #00d4ff !important; box-shadow: 0 0 0 1px rgba(0,212,255,0.28) !important; }"
        'div[data-testid="stTextArea"] textarea::placeholder { color: #64748b !important; }'
        '[data-testid="stSidebar"] .stSelectbox > div > div { '
        "background: #1e293b !important; border: 1px solid rgba(0,212,255,0.22) !important; "
        "color: #f1f5f9 !important; }"
        '[data-testid="stMetric"] { '
        "background: rgba(15,23,42,0.55); border: 1px solid rgba(0,212,255,0.14); "
        "border-radius: 10px; padding: 0.8rem; }"
        '[data-testid="stMetricValue"] { color: #00d4ff; font-weight: 700; }'
        '[data-testid="stMetricLabel"] { '
        "color: #94a3b8; text-transform: uppercase; letter-spacing: 0.05em; font-size: 0.75rem; }"
        ".stSpinner > div { border-top-color: #00d4ff !important; }"
        "footer { margin-top: 2.5rem; padding: 1.2rem 0; "
        "border-top: 1px solid rgba(0,212,255,0.12); "
        "text-align: center; color: #64748b; font-size: 0.8rem; }"
        "</style>"
    )


st.markdown(_make_css(_bg_css), unsafe_allow_html=True)

st.markdown('<div class="atlas-title">Atlas for SAP</div>', unsafe_allow_html=True)
st.markdown('<div class="atlas-sub">Autonomous SAP Fiori discovery & Q&A agent</div>', unsafe_allow_html=True)

if "last_result" not in st.session_state:
    st.session_state["last_result"] = None

with st.sidebar:
    st.header("Connection")
    import os as _os

    _default_url = _os.environ.get("SAP_AGENT_URL", "https://jonasperegrino.github.io/sap-fiori/")
    _env_password = _os.environ.get("SAP_AGENT_PASSWORD", "")
    # Form batches sidebar edits: without it every keystroke reruns the app
    # and re-renders CSS + background.
    with st.form("connection"):
        app_url = st.text_input("App URL", value=_default_url)
        username = st.text_input("Username", value="demo")
        password = st.text_input(
            "Password",
            type="password",
            value=_env_password,
            placeholder="password123 (demo)",
        )
        _env_key = _os.environ.get("SAP_AGENT_LLM_API_KEY", "") or _os.environ.get("OPENAI_API_KEY", "")
        llm_default_model = _os.environ.get("SAP_AGENT_LLM_MODEL", "gpt-5")
        llm_api_key_input = st.text_input(
            "OpenAI API Key",
            type="password",
            value="",
            placeholder="sk-..." if not _env_key else "using key from secrets",
            help="For aggregate questions. Leave empty for deterministic mode.",
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
        st.form_submit_button("Apply", use_container_width=True)
    st.caption("Demo: demo / password123")
    st.caption("Credentials are used for this run only. Agent auto-finds the right page.")

    st.divider()
    st.subheader("AI / LLM (optional)")
    # Prefill from env/secrets if present, else empty
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
        elif not password:
            st.warning("Enter the demo password in the sidebar (demo: password123).")
        else:
            cfg = Config.from_env(app_url=app_url, username=username, password=password)
            # Fast single-attempt mode for interactive use (full budgets stay in CLI).
            cfg.login_timeout_ms = 8000
            cfg.retry_budget = 1
            st.caption("Fast mode: 8s login window, single attempt (CLI uses full budgets).")
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
                with st.status("Running agent…", expanded=True) as status:
                    st.write("Logging in…")
                    res = run_question(cfg, question.strip(), None)
                    st.write("Rendering answer…")
                    status.update(label="Agent run complete", state="complete")
                st.session_state["last_result"] = res
            except Exception as e:
                if "not reachable" in str(e).lower() or "Failed to establish" in str(e):
                    st.error(f"App not reachable at {app_url}: {e}")
                else:
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
                    f"**{rec.get('contact', '')}** — {rec.get('contactTitle', '')}"
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
            import json as _json

            _trace = res.trace or []
            st.json({"evidence": a.evidence.model_dump(), "checksum": a.checksum, "trace": _trace[-20:]})
            if len(_trace) > 20:
                st.caption(f"Showing last 20 of {len(_trace)} trace entries.")
            st.download_button(
                "Download full trace",
                _json.dumps(_trace, indent=2),
                file_name="trace.json",
                key="dl_trace",
            )
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
    reps = sorted(
        [p for pat in ("bug_report.md", "qa_report.md", "qa_report.json") for p in adir.glob(pat)],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not reps:
        st.info("No automatic bug or QA reports have been generated yet.")
    else:
        names = [p.name for p in reps]
        pick = st.selectbox("Report", names, index=0)
        rp = next(p for p in reps if p.name == pick)
        st.caption(str(rp))
        try:
            text = rp.read_text()
        except OSError as e:
            st.error(f"Could not read {rp.name}: {e}")
        else:
            if len(text) > 8_000:
                st.markdown(text[:8_000])
                st.caption(f"Truncated: showing 8,000 of {len(text)} chars — download for full report.")
            else:
                st.markdown(text) if rp.suffix == ".md" else st.code(text[:8_000], language="json")
            st.download_button("Download report", rp.read_bytes(), file_name=rp.name, key=f"dl_{rp.name}")
