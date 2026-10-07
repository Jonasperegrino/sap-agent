"""Atlas for SAP — Ask Atlas / Bug reports tabs. Streamlit natives plus one scoped stylesheet (ui.components)."""

from __future__ import annotations

import json as _json
import logging
import os as _os
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import streamlit as st

from sap_agent import demo_case as demo
from sap_agent.ui import components as ui

# Must be the first Streamlit call.
st.set_page_config(page_title="Atlas for SAP", page_icon="◈", layout="centered")

logger = logging.getLogger("fiori-agent")

if TYPE_CHECKING:
    from sap_agent.schemas import Config as ConfigModel

# Engine must NEVER take the UI down: guarded import, inline degrade.
_ENGINE_ERROR = ""
try:
    from sap_agent.schemas import Config
    from sap_agent.ui.service import RunResult, run_question
except Exception as exc:
    logger.warning("agent engine unavailable, UI running degraded: %s", exc)
    Config: Any = Any
    RunResult: Any = Any

    _ENGINE_ERROR = f"{type(exc).__name__}: {exc}"[:200]

    def run_question(*args: Any, **kwargs: Any) -> Any:  # noqa: ARG001
        raise RuntimeError(f"Agent engine unavailable: {_ENGINE_ERROR}")


logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

# Streamlit Cloud: inject secrets into env so Config.from_env picks them up
try:
    for _k in ("SAP_AGENT_URL", "SAP_AGENT_USER", "SAP_AGENT_PASSWORD", "SAP_AGENT_LLM_API_KEY"):
        if _k in st.secrets and _k not in _os.environ:
            _os.environ[_k] = str(st.secrets[_k])
except (RuntimeError, OSError, AttributeError, ValueError, TypeError, KeyError) as exc:
    logger.debug("secret injection skipped: %s", exc)


def _chromium_ready() -> bool:
    """Non-blocking check for Playwright Chromium (background install, never block render)."""
    import pathlib as _pl2

    from sap_agent.browser import ensure_chromium_install_started

    bases = [
        _pl2.Path.home() / ".cache" / "ms-playwright",
        _pl2.Path.home() / "Library" / "Caches" / "ms-playwright",
        _pl2.Path("/home/appuser/.cache/ms-playwright"),
    ]
    if any(_b.exists() and any(_b.glob("chromium*")) for _b in bases):
        return True
    ensure_chromium_install_started()
    return False


def _story_root() -> Path:
    for cand in (
        Path(__file__).resolve().parents[2] / "artifacts" / "story",
        Path.cwd() / "artifacts" / "story",
    ):
        if (cand / "uc1").exists() or (cand / "uc2").exists() or (cand / "uc3").exists():
            return cand
    return Path(__file__).resolve().parents[2] / "artifacts" / "story"


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        return _json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _shot(path: Path) -> Path | None:
    try:
        if path.exists() and path.stat().st_size > 1024:
            return path
    except OSError:
        pass
    return None


def _eur(value: Any) -> str:
    try:
        return f"€{float(value):,.2f}"
    except (TypeError, ValueError):
        return "—"


def _demo_delay() -> float:
    """Pacing for video narration. Pacing only — evidence always real."""
    try:
        return max(0.0, float(_os.environ.get("SAP_AGENT_DEMO_DELAY", "0.5")))
    except (TypeError, ValueError):
        return 0.5


_STORY = _story_root()
_ASSETS = Path(__file__).resolve().parent / "assets"

st.html(ui.STYLE)
st.html(ui.header_html("Atlas for SAP", "Evidence-backed"))

if "last_result" not in st.session_state:
    st.session_state["last_result"] = None

# Start the Chromium download when the app boots, not on the first live question.
if not _ENGINE_ERROR:
    try:
        _chromium_ready()
    except Exception as exc:
        logger.debug("chromium warm-up skipped: %s", exc)


def _demo_summary(result: dict[str, Any]) -> tuple[str, list[ui.Chip], str]:
    """Lead sentence, fact chips and recommendation for the SO-1024 finding."""
    case = result["case"]
    lead = (
        f"Total Value is {result['overstatement_pct']:.1f}% too high. The dashboard counts {case['order_id']} as "
        f"{_eur(case['stored_amount_eur'])}; its order row shows {_eur(case['display_amount_eur'])}."
    )
    chips: list[ui.Chip] = [
        {"text": str(case["order_id"]), "tone": "neutral"},
        {"text": f"€{result['discrepancy_eur']:,.0f} variance", "tone": "amber"},
        {"text": "HIGH · PRODUCT BUG", "tone": "red"},
        {"text": "✓ Bug report ready", "tone": "blue"},
    ]
    return lead, chips, "Recommended action · hold revenue report"


_DEMO_NOTE = (
    "Demo scenario. The Fiori figures come from a seeded snapshot of the demo app; the ingestion log is simulated."
)

_SHOW_INVESTIGATE_TAB = _os.environ.get("SAP_AGENT_SHOW_INVESTIGATE_TAB", "").strip().lower() in {
    "1",
    "true",
    "yes",
}
_tab_labels = ["Ask Atlas", "Bug reports"]
if _SHOW_INVESTIGATE_TAB:
    _tab_labels.insert(0, "Investigate")
_tabs = st.tabs(_tab_labels, key="atlas_tabs", on_change="rerun")
if _SHOW_INVESTIGATE_TAB:
    tab_story, tab_ask, tab_reports = _tabs
else:
    tab_story = None
    tab_ask, tab_reports = _tabs

with tab_ask:
    # An empty submit runs the example: the field is filled on the rerun, then asked.
    _run_example = bool(st.session_state.pop("ask_example_pending", False))
    if _run_example:
        st.session_state["q_input"] = demo.QUESTION

    question = st.text_area(
        "Your question",
        key="q_input",
        placeholder="Ask about SAP data, or ask why the dashboard Total Value differs from the order amounts.",
        height=96,
    )
    _, _mid, _ = st.columns([1.3, 1, 1.3])
    with _mid:
        ask = st.button("Get answer", type="primary", width="stretch")

    if ask and not (question or "").strip():
        st.session_state["ask_example_pending"] = True
        st.rerun()

    # Everything the agent reports lands in this slot; a new question clears it at once.
    _out = st.empty()
    _animate_demo = False
    if ask or _run_example:
        _out.empty()
        st.session_state["last_result"] = None
        st.session_state["ask_demo_result"] = None
        if demo.matches_demo_question(question or ""):
            _fresh = demo.analyze_demo_ingestion()
            st.session_state["ask_demo_result"] = _fresh
            st.session_state["story_result"] = _fresh
            st.session_state["story_report"] = demo.report_markdown(_fresh)
            _animate_demo = True
        elif _ENGINE_ERROR:
            st.error(
                f"Agent engine failed to load on the server ({_ENGINE_ERROR}). "
                "The page itself is fine — check deployment logs or redeploy, then retry."
            )
        elif not _chromium_ready():
            st.warning("The browser engine is still starting (first start only). Try again in about 30 seconds.")
        else:
            cfg = cast("ConfigModel", Config).from_env(
                app_url=_os.environ.get("SAP_AGENT_URL", "https://jonasperegrino.github.io/sap-fiori/"),
                username=_os.environ.get("SAP_AGENT_USER", "demo"),
                password=_os.environ.get("SAP_AGENT_PASSWORD", "") or "password123",
            )
            cfg.login_timeout_ms = 8000
            cfg.retry_budget = 1
            _out.html(ui.status_html("Running agent…", running=True))
            try:
                st.session_state["last_result"] = run_question(cfg, question.strip(), None)
                st.session_state["last_question"] = question.strip()
            except Exception as e:
                if "not reachable" in str(e).lower() or "Failed to establish" in str(e):
                    st.error(f"App not reachable: {e}")
                else:
                    st.error(f"Agent crashed: {e}")
            _out.empty()

    _demo_result = st.session_state.get("ask_demo_result")
    if _demo_result:
        with _out.container():
            import time as _time

            _rows = demo.trace_rows(_demo_result)
            _status_slot, _evidence_slot, _ready_slot = st.empty(), st.empty(), st.empty()
            _ready_right = f"{_demo_result['case']['order_id']} · Currency mapping defect"
            if _animate_demo:
                # The agent's steps arrive one by one, in the order they were taken.
                _d = _demo_delay()
                _status_slot.html(ui.status_html("Running agent…", running=True, animate=True))
                _time.sleep(_d)
                _evidence_slot.html(ui.evidence_html([]))
                _time.sleep(_d * 0.6)
                for _i in range(len(_rows)):
                    _evidence_slot.html(ui.evidence_html(_rows[: _i + 1], newest=_i))
                    _time.sleep(_d)
                _status_slot.html(ui.status_html("Agent run complete", running=False))
                _time.sleep(_d * 0.8)
                _ready_slot.html(ui.status_html("Bug report ready", running=False, right=_ready_right, animate=True))
                _time.sleep(_d * 0.8)
            else:
                _status_slot.html(ui.status_html("Agent run complete", running=False))
                _evidence_slot.html(ui.evidence_html(_rows))
                _ready_slot.html(ui.status_html("Bug report ready", running=False, right=_ready_right))

            _lead, _chips, _rec = _demo_summary(_demo_result)
            st.html(ui.answer_html("Currency mapping defect", _lead, _chips))
            with st.container(horizontal=True, vertical_alignment="center"):
                st.html(ui.recommendation_html(_rec), width="content")
                st.download_button(
                    "Download report",
                    st.session_state.get("story_report", ""),
                    file_name="SO-1024-currency-mapping-report.md",
                    mime="text/markdown",
                    key="download_demo_report_from_ask",
                )
            st.html(ui.note_html(_DEMO_NOTE))

    res = st.session_state.get("last_result")
    if not _demo_result and res and res.answer:
        with _out.container():
            a = res.answer
            st.html(ui.status_html("Agent run complete", running=False))
            if a.unsupported:
                st.warning(a.message or "Question not supported.")
                if not (_os.environ.get("SAP_AGENT_LLM_API_KEY") or _os.environ.get("OPENAI_API_KEY")):
                    st.info("Tip: aggregate questions need an LLM key (SAP_AGENT_LLM_API_KEY).")
            elif a.not_found:
                st.info(a.message or "No matching rows found.")
            else:
                _conf = (a.confidence or "none").strip() or "none"
                _live_rows: list[ui.Row] = [
                    {"label": "Read from", "value": a.evidence.source or "—", "tone": "plain"},
                    {"label": "Question type", "value": a.intent.value, "tone": "plain"},
                    {"label": "Rows matched", "value": str(a.evidence.matched_rows), "tone": "plain"},
                    {"label": "Checksum", "value": f"{a.checksum[:12]}… ok", "tone": "plain"},
                ]
                st.html(ui.evidence_html(_live_rows))
                _asked = str(st.session_state.get("last_question", "")).strip()
                if isinstance(a.answer, list):
                    st.html(ui.answer_html(_asked or "Answer", "", [{"text": f"confidence: {_conf}", "tone": "blue"}]))
                    st.dataframe(a.answer, width="stretch")
                else:
                    st.html(
                        ui.answer_html(
                            str(a.answer),
                            _asked,
                            [
                                {"text": f"confidence: {_conf}", "tone": "blue"},
                                {"text": f"{a.evidence.matched_rows} rows read", "tone": "neutral"},
                            ],
                        )
                    )
            with st.expander("Full agent trace"):
                _ev = a.evidence.model_dump()
                st.dataframe([{"field": k, "value": str(v)} for k, v in _ev.items()], width="stretch")
                _trace = res.trace or []
                if _trace:
                    st.dataframe(_trace[-20:], width="stretch")
                st.download_button("Download full trace", _json.dumps(_trace, indent=2), file_name="trace.json")
    elif not _demo_result and res and res.report:
        with _out.container():
            if res.report.classification.value == "unsupported_auth_flow" or "Invalid credentials" in (
                res.error or res.report.actual or ""
            ):
                st.error("Login failed — invalid credentials.")
            else:
                st.error(f"Run failed: {res.report.classification.value}")
                st.write(res.error or res.report.actual)
            if res.report_path and Path(res.report_path).exists():
                _rp = Path(res.report_path)
                st.download_button("Download bug report", _rp.read_bytes(), file_name=_rp.name)


def _render_investigate_tab() -> None:
    _names = {
        "Currency bug": "SO-1024 · Currency mapping investigation",
        "UC1": "Trust the number",
        "UC2": "Sweep the app",
        "UC3": "Explain the gap",
    }
    _rr_l, _rr_r = st.columns([3, 1])
    with _rr_r:
        if st.button("Replay investigation", icon=":material/play_arrow:", key="story_replay"):
            st.session_state["story_result"] = None
            st.session_state["story_report"] = ""
            st.session_state["ask_demo_result"] = None
            st.session_state["story_uc"] = "Currency bug"
            st.rerun()
    with _rr_l:
        st.caption("Seeded Fiori snapshot · simulated backend diagnostic")
    uc = st.segmented_control("Scenario", ["Currency bug", "UC1", "UC2", "UC3"], default="Currency bug", key="story_uc")
    st.caption(_names.get(uc or "", ""))
    if uc == "Currency bug":
        analyze_demo_ingestion, report_markdown = demo.analyze_demo_ingestion, demo.report_markdown

        st.subheader("Why is the dashboard total different?")
        with st.container(border=True):
            st.caption("QUESTION")
            st.write(demo.QUESTION)
        if st.button("Run investigation", type="primary", icon=":material/search:", key="run_story_investigation"):
            import time as _time

            with st.status("Investigating order value…", expanded=True) as _status:
                st.write("Reading the seeded Fiori order snapshot…")
                _time.sleep(_demo_delay())
                st.write("Comparing the order row with the dashboard KPI…")
                _result = analyze_demo_ingestion()
                _time.sleep(_demo_delay())
                st.write("Running the simulated non-EU ingestion check…")
                _time.sleep(_demo_delay())
                st.write("Preparing a combined bug report…")
                _status.update(label="Investigation complete", state="complete")
            st.session_state["story_result"] = _result
            st.session_state["story_report"] = report_markdown(_result)

        _result = st.session_state.get("story_result")
        if _result:
            _case = _result["case"]
            _front = _result["frontend"]
            _back = _result["backend"]
            st.warning("Hold the revenue report · KPI overstatement of " + _eur(_result["discrepancy_eur"]))
            _m1, _m2, _m3 = st.columns(3)
            _m1.metric("Fiori order row", _eur(_case["display_amount_eur"]))
            _m2.metric("KPI contribution", _eur(_case["stored_amount_eur"]))
            _m3.metric("Variance", "+" + _eur(_result["discrepancy_eur"]))

            with st.container(border=True):
                _left, _right = st.columns([1, 2])
                with _left:
                    st.badge("Fiori snapshot · seeded case", icon=":material/monitoring:", color="orange")
                    st.write("Fiori row and KPI disagree")
                    st.caption(f"{_case['order_id']} · {_case['customer']} · {_case['status']}")
                with _right:
                    st.write(_front["actual"])
                    st.dataframe(
                        [
                            {"Field": "Order row amount", "Value": _eur(_case["display_amount_eur"])},
                            {"Field": "KPI source · amountEur", "Value": _eur(_case["stored_amount_eur"])},
                            {"Field": "Difference", "Value": _eur(_result["discrepancy_eur"])},
                        ],
                        hide_index=True,
                        width="stretch",
                    )

            with st.container(border=True):
                _left, _right = st.columns([1, 2])
                with _left:
                    st.badge("SIMULATED BACKEND RUNNER", icon=":material/terminal:", color="blue")
                    st.write("Currency normalization failed")
                    st.caption("Deterministic diagnostic · no live ingestion service")
                with _right:
                    st.code(
                        f"sourceAmount = {_case['source_currency']} {_case['source_amount']:,.2f}\n"
                        f"fxRateToEur = {_case['fx_rate_to_eur']}\n"
                        f"expected amountEur = {_eur(_result['expected_amount_eur'])}\n"
                        f"stored amountEur = {_eur(_case['stored_amount_eur'])}\n"
                        f"ingestionPath = {_case['ingestion_path']}  # raw amount copied without conversion",
                        language="text",
                    )
                    st.write(_back["actual"])

            st.success(_result["decision"])
            st.download_button(
                "Download combined bug report",
                st.session_state.get("story_report", ""),
                file_name="SO-1024-currency-mapping-report.md",
                mime="text/markdown",
                key="download_story_report",
            )
        else:
            st.info("Run the investigation to compare the Fiori value and inspect the simulated ingestion diagnostic.")
    elif uc == "UC3":
        st.write("3 · Explain the gap — KPI vs recomputed revenue, delta fully explained.")
        d = _load_json(_STORY / "uc3" / "answer.json")
        if not d:
            st.info("No artifacts yet — run: fiori-agent story")
        else:
            st.badge(f"data: {d.get('verdict', 'unknown')}", icon=":material/flag:", color="orange")
            _expl = d.get("explained_by", []) or []
            _rej = _expl[0].get("amount") if len(_expl) > 0 else None
            _can = _expl[1].get("amount") if len(_expl) > 1 else None
            m1, m2 = st.columns(2)
            m1.metric("KPI Total Value (Gross)", _eur(d.get("kpi")))
            m2.metric("Revenue 2026", _eur(d.get("revenue")))
            m3, m4 = st.columns(2)
            m3.metric("Rejected SO-1006", f"−{_eur(_rej)}" if _rej else "—")
            m4.metric("Cancelled SO-1011", f"−{_eur(_can)}" if _can else "—")
            _l, _r = st.columns(2)
            with _l, st.container(border=True):
                st.write("AI decided")
                st.badge("provider: rules", color="blue")
                st.write(f"verdict {d.get('verdict', '—')} · severity {d.get('severity', '—')}")
            with _r, st.container(border=True):
                st.write("Code proved")
                st.write(str(d.get("heads_up", "")))
                try:
                    _kpi = float(d.get("kpi", 0) or 0)
                    _rj = float(_rej or 0)
                    _rev = float(d.get("revenue", 0) or 0)
                    st.bar_chart([_kpi, _kpi - _rj, _rev])
                except (TypeError, ValueError):
                    pass
            _kp = _shot(_STORY / "uc3" / "kpi.png")
            if _kp:
                st.image(str(_kp), caption="Dashboard KPI vs recomputed revenue")
    elif uc == "UC1":
        st.write("1 · Trust the number — customer exposure with definition + checksum.")
        d = _load_json(_STORY / "uc1" / "answer.json")
        if not d:
            st.info("No artifacts yet — run: fiori-agent story")
        else:
            st.badge(f"answer: {_eur(d.get('total'))} open", icon=":material/verified:", color="green")
            m1, m2 = st.columns(2)
            m1.metric("Open orders", _eur(d.get("total")))
            m2.metric("Sept 2026 revenue", _eur(d.get("sept_2026_revenue")))
            _l, _r = st.columns(2)
            with _l, st.container(border=True):
                st.write("AI decided")
                st.badge("provider: rules", color="blue")
                st.write(str(d.get("open_definition", "")))
            with _r, st.container(border=True):
                st.write("Code proved")
                st.write(f"checksum {str(d.get('checksum', ''))[:12]}… ok")
                if isinstance(d.get("breakdown"), list):
                    st.dataframe(d["breakdown"], width="stretch")
            for _img, _cap in (
                (_STORY / "uc1" / "dashboard.png", "Dashboard orders view"),
                (_STORY / "uc1" / "customer-detail.png", "Customer detail drill-down"),
            ):
                _sp = _shot(_img)
                if _sp:
                    st.image(str(_sp), caption=_cap)
    else:
        st.write("2 · Sweep the app — full-route audit, blocker blocks shipment.")
        d = _load_json(_STORY / "uc2" / "answer.json")
        if not d:
            st.info("No artifacts yet — run: fiori-agent story")
        else:
            _v = str(d.get("verdict", "unknown"))
            st.badge(f"ship: {_v}", icon=":material/block:", color="red" if _v == "no-go" else "gray")
            _l, _r = st.columns(2)
            with _l, st.container(border=True):
                st.write("AI decided")
                st.badge("provider: rules", color="blue")
                st.write(f"classification {d.get('classification', '—')}")
            with _r, st.container(border=True):
                st.write("Code proved")
                _issues = d.get("issues", []) or []
                st.write(f"{len(_issues)} issue file{'s' if len(_issues) != 1 else ''}")
                if _issues:
                    st.dataframe([{"issue": str(i)} for i in _issues], width="stretch")
            _rp = _shot(_STORY / "uc2" / "regression.png")
            if _rp:
                st.image(str(_rp), caption="Regression evidence")


if _SHOW_INVESTIGATE_TAB and tab_story is not None:
    with tab_story:
        _render_investigate_tab()

with tab_reports:
    if tab_reports.open:
        _report_result = st.session_state.get("story_result")
        _story_report = st.session_state.get("story_report", "")
        if not _report_result or not _story_report:
            st.html(
                ui.empty_html(
                    "No bug reports yet",
                    "Ask Atlas why the dashboard Total Value differs from the order amounts. "
                    "The report it drafts will appear here.",
                )
            )
        else:
            _case = _report_result["case"]
            _lead, _chips, _rec = _demo_summary(_report_result)
            st.html(
                ui.status_html(
                    "Bug report ready", running=False, right=f"{_case['order_id']} · Currency mapping defect"
                )
            )
            st.html(ui.answer_html(f"{_case['order_id']} · Currency mapping defect", _lead, _chips))
            st.html(ui.recommendation_html(_rec))
            st.write(
                f"A new non-EU order for **{_case['customer']}** entered as "
                f"**{_case['source_currency']} {_case['source_amount']:,.2f}** via "
                f"`{_case['ingestion_path']}`. Currency normalization was skipped, so the raw amount was stored "
                f"as euros. Total Value reads {_eur(_case['dashboard_total_eur'])} against "
                f"{_eur(_case['order_rows_sum_eur'])} in the order rows."
            )
            st.write(_report_result["decision"])

            st.subheader("Evidence")
            _fiori_capture = _shot(_ASSETS / "so-1024-dashboard.png") or _shot(_STORY / "uc4" / "fiori_dashboard.png")
            if _fiori_capture:
                st.image(
                    str(_fiori_capture),
                    caption="Fiori sales dashboard: Total Value, sum of amounts and SO-1024 in the first row",
                    width="stretch",
                )
            st.html(ui.evidence_html(demo.trace_rows(_report_result)[:3], title="Fiori dashboard", heading="h4"))
            _ingestion_rows: list[ui.Row] = [
                {"label": "orderId", "value": str(_case["order_id"]), "tone": "plain"},
                {
                    "label": "sourceAmount",
                    "value": f"{_case['source_currency']} {_case['source_amount']:,.2f}",
                    "tone": "plain",
                },
                {"label": "fxRateToEur", "value": str(_case["fx_rate_to_eur"]), "tone": "plain"},
                {"label": "ingestionPath", "value": str(_case["ingestion_path"]), "tone": "plain"},
                {
                    "label": "expected amountEur",
                    "value": _eur(_report_result["expected_amount_eur"]),
                    "tone": "plain",
                },
                {"label": "amountEur persisted", "value": _eur(_case["stored_amount_eur"]), "tone": "bad"},
            ]
            st.html(ui.evidence_html(_ingestion_rows, title="Ingestion log · simulated", heading="h4"))

            with st.expander("Full bug report"):
                st.markdown(_story_report)
            st.download_button(
                "Download report",
                _story_report,
                file_name="SO-1024-currency-mapping-report.md",
                mime="text/markdown",
                type="primary",
                key="download_story_report_tab",
            )
            st.html(ui.note_html(_DEMO_NOTE))
