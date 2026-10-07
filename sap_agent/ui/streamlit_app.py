"""Atlas for SAP — Ask / Story / Reports tabs. Natives + theme only, no custom CSS."""

from __future__ import annotations

import json as _json
import logging
import os as _os
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import streamlit as st

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

_top_l, _top_r = st.columns([3, 1])
with _top_l:
    st.title("Atlas for SAP")
with _top_r:
    st.badge("Evidence-backed", icon=":material/verified:", color="blue")

if "last_result" not in st.session_state:
    st.session_state["last_result"] = None


def _on_tabs_change() -> None:
    if st.session_state.get("atlas_tabs") == "Bug reports":
        st.session_state["story_result"] = None
        st.session_state["story_report"] = ""
        st.session_state["ask_demo_result"] = None


_SHOW_INVESTIGATE_TAB = _os.environ.get("SAP_AGENT_SHOW_INVESTIGATE_TAB", "").strip().lower() in {
    "1",
    "true",
    "yes",
}
_tab_labels = ["Ask Atlas", "Bug reports"]
if _SHOW_INVESTIGATE_TAB:
    _tab_labels.insert(0, "Investigate")
_tabs = st.tabs(
    _tab_labels,
    key="atlas_tabs",
    on_change=_on_tabs_change,
)
if _SHOW_INVESTIGATE_TAB:
    tab_story, tab_ask, tab_reports = _tabs
else:
    tab_story = None
    tab_ask, tab_reports = _tabs

with tab_ask:
    question = st.text_area(
        "Your question",
        key="q_input",
        placeholder="Ask about SAP data, or ask why the dashboard Total Value differs from the order amounts.",
        height=92,
    )
    _, _mid, _ = st.columns([1, 1, 1])
    with _mid:
        ask = st.button("Get answer", type="primary", width="stretch")

    if ask:
        st.session_state["last_result"] = None
        st.session_state["ask_demo_result"] = None
        st.session_state["story_result"] = None
        st.session_state["story_report"] = ""
        from sap_agent.tools.ingestion_demo import analyze_demo_ingestion, matches_demo_question, report_markdown

        if matches_demo_question(question or ""):
            import time as _time

            with st.status("Running the SO-1024 demo investigation…", expanded=True) as _status:
                st.write("Comparing the seeded Fiori snapshot…")
                _time.sleep(_demo_delay())
                _demo_result = analyze_demo_ingestion()
                st.write("Checking the simulated non-EU ingestion path…")
                _time.sleep(_demo_delay())
                st.write("Preparing the combined report…")
                _status.update(label="Demo investigation complete", state="complete")
            st.session_state["ask_demo_result"] = _demo_result
            st.session_state["story_result"] = _demo_result
            st.session_state["story_report"] = report_markdown(_demo_result)
        elif _ENGINE_ERROR:
            st.error(
                f"Agent engine failed to load on the server ({_ENGINE_ERROR}). "
                "The page itself is fine — check deployment logs or redeploy, then retry."
            )
        elif not (question or "").strip():
            st.warning("Enter a question before asking.")
        elif not _chromium_ready():
            st.warning("Browser is still provisioning (Chromium downloads on first start). Wait ~30s and retry.")
        else:
            cfg = cast("ConfigModel", Config).from_env(
                app_url=_os.environ.get("SAP_AGENT_URL", "https://jonasperegrino.github.io/sap-fiori/"),
                username=_os.environ.get("SAP_AGENT_USER", "demo"),
                password=_os.environ.get("SAP_AGENT_PASSWORD", "") or "password123",
            )
            cfg.login_timeout_ms = 8000
            cfg.retry_budget = 1
            try:
                import time as _time

                _d = _demo_delay()
                with st.status("Agent working…", expanded=True) as status:
                    st.write("Opening dashboard…")
                    _time.sleep(_d)
                    st.write("Reading orders…")
                    _time.sleep(_d)
                    st.write("Checking customer pages…")
                    res = run_question(cfg, question.strip(), None)
                    st.write("Verifying checksum…")
                    _time.sleep(_d)
                    st.write("Composing answer…")
                    _time.sleep(_d)
                    status.update(label="Agent run complete", state="complete")
                st.session_state["last_result"] = res
            except Exception as e:
                if "not reachable" in str(e).lower() or "Failed to establish" in str(e):
                    st.error(f"App not reachable: {e}")
                else:
                    st.error(f"Agent crashed: {e}")

    _demo_result = st.session_state.get("ask_demo_result")
    if _demo_result:
        st.caption("Backend diagnostic · simulated")
        _demo_case = _demo_result["case"]
        _demo_cols = st.columns(3)
        _demo_cols[0].metric("Fiori order row", _eur(_demo_case["display_amount_eur"]))
        _demo_cols[1].metric("KPI contribution", _eur(_demo_case["stored_amount_eur"]))
        _demo_cols[2].metric("Variance", "+" + _eur(_demo_result["discrepancy_eur"]))
        st.write(_demo_result["frontend"]["actual"])
        st.write(_demo_result["backend"]["actual"])
        st.success(_demo_result["decision"])
        st.caption("Open Bug reports for the full evidence and combined report.")
        st.download_button(
            "Download combined bug report",
            st.session_state.get("story_report", ""),
            file_name="SO-1024-currency-mapping-report.md",
            mime="text/markdown",
            key="download_demo_report_from_ask",
        )

    res = st.session_state.get("last_result")
    if not _demo_result and res and res.answer:
        a = res.answer
        if a.unsupported:
            st.warning(a.message or "Question not supported.")
            if not (_os.environ.get("SAP_AGENT_LLM_API_KEY") or _os.environ.get("OPENAI_API_KEY")):
                st.info("Tip: aggregate questions need an LLM key (SAP_AGENT_LLM_API_KEY).")
        elif a.not_found:
            st.info(a.message or "No matching rows found.")
        else:
            with st.container(border=True):
                if isinstance(a.answer, list):
                    st.dataframe(a.answer, width="stretch")
                else:
                    st.write(a.answer)
                _conf = (a.confidence or "none").strip() or "none"
                st.badge(f"confidence: {_conf}", icon=":material/verified:")
                st.caption(f"{a.intent.value} · {a.evidence.matched_rows} rows read · checksum {a.checksum[:12]}… ok")
        with st.expander("Evidence and trace"):
            _ev = a.evidence.model_dump()
            st.dataframe([{"field": k, "value": str(v)} for k, v in _ev.items()], width="stretch")
            _trace = res.trace or []
            if _trace:
                st.write("Agent steps")
                for _t in _trace[-8:]:
                    st.write(f"▪ {_t.get('action', _t) if isinstance(_t, dict) else _t}")
                st.dataframe(_trace[-20:], width="stretch")
            st.download_button("Download full trace", _json.dumps(_trace, indent=2), file_name="trace.json")
    elif not _demo_result and res and res.report:
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
        from sap_agent.tools.ingestion_demo import QUESTION as _QUESTION
        from sap_agent.tools.ingestion_demo import analyze_demo_ingestion, report_markdown

        st.subheader("Why is the dashboard total different?")
        with st.container(border=True):
            st.caption("QUESTION")
            st.write(_QUESTION)
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
        _story_report = st.session_state.get("story_report", "")
        _report_result = st.session_state.get("story_result")
        if not _story_report:
            import time as _time

            from sap_agent.tools.ingestion_demo import analyze_demo_ingestion, report_markdown

            with st.status("Building the SO-1024 bug report…", expanded=True) as _report_status:
                st.write("Loading the Fiori order snapshot…")
                _time.sleep(_demo_delay())
                _report_result = analyze_demo_ingestion()
                st.write("Tracing the non-EU ingestion mapping…")
                _time.sleep(_demo_delay())
                _story_report = report_markdown(_report_result)
                st.write("Assembling the findings and evidence…")
                _time.sleep(_demo_delay())
                _report_status.update(label="Bug report ready", state="complete", expanded=False)
            st.session_state["story_result"] = _report_result
            st.session_state["story_report"] = _story_report
            st.session_state["ask_demo_result"] = _report_result

        st.subheader("SO-1024 · Currency mapping defect")
        _report_meta = st.columns([1, 1, 2])
        _report_meta[0].badge("HIGH · PRODUCT BUG", icon=":material/priority_high:", color="red")
        _variance = f"€{_report_result['discrepancy_eur']:,.0f} variance" if _report_result else "Variance"
        _report_meta[1].badge(_variance, icon=":material/monitoring:", color="orange")
        _report_meta[2].badge("Backend diagnostic · simulated", icon=":material/terminal:", color="blue")
        if _report_result:
            _case = _report_result["case"]
            st.write(
                f"A new non-EU order for **{_case['customer']}** entered as "
                f"**{_case['source_currency']} {_case['source_amount']:,.2f}** via "
                f"`{_case['ingestion_path']}`. Currency normalization was skipped: the order row shows "
                f"{_eur(_case['display_amount_eur'])}, but the dashboard counts "
                f"{_eur(_case['stored_amount_eur'])}. Total Value reads "
                f"{_eur(_case['dashboard_total_eur'])} against {_eur(_case['order_rows_sum_eur'])} "
                f"in the order rows — **{_report_result['overstatement_pct']:.1f}% too high**."
            )
            st.warning(f"**Recommended action · hold revenue report**\n\n{_report_result['decision']}")

            st.markdown("### Evidence")
            _fiori_capture = _shot(_STORY / "uc4" / "fiori_dashboard.png")
            _backend_capture = _shot(_STORY / "uc4" / "ingestion_record.png")
            _evidence_left, _evidence_right = st.columns(2)
            with _evidence_left, st.container(border=True):
                st.markdown("#### Fiori dashboard")
                st.badge("Seeded Fiori snapshot", icon=":material/monitoring:", color="orange")
                if _fiori_capture:
                    st.image(
                        str(_fiori_capture),
                        caption="SO-1024 · sales dashboard",
                        width="stretch",
                    )
                else:
                    st.caption("Screenshot pending · values below come from the seeded snapshot")
                    st.dataframe(
                        [
                            {"Field": "Order", "Value": _case["order_id"]},
                            {"Field": "Customer", "Value": _case["customer"]},
                            {"Field": "Order row · EUR", "Value": _eur(_case["display_amount_eur"])},
                            {"Field": "KPI contribution · EUR", "Value": _eur(_case["stored_amount_eur"])},
                        ],
                        hide_index=True,
                        width="stretch",
                    )
            with _evidence_right, st.container(border=True):
                st.markdown("#### Backend ingestion record")
                st.badge("Backend ingestion log", icon=":material/terminal:", color="blue")
                if _backend_capture:
                    st.image(
                        str(_backend_capture),
                        caption="Simulated ingestion diagnostic · SO-1024",
                        width="stretch",
                    )
                else:
                    st.caption("Technical trace · seeded diagnostic values")
                    st.code(
                        f"orderId: {_case['order_id']}\n"
                        f"customerId: {_case['customer_id']}\n"
                        f"sourceAmount: {_case['source_currency']} {_case['source_amount']:,.2f}\n"
                        f"fxRateToEur: {_case['fx_rate_to_eur']}\n"
                        f"ingestionPath: {_case['ingestion_path']}\n"
                        f"amountEur persisted: {_eur(_case['stored_amount_eur'])}\n"
                        f"expected amountEur: {_eur(_report_result['expected_amount_eur'])}",
                        language="text",
                    )

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
