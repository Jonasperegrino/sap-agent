"""Unit tests for LLM intent parsing (issue #647 extension)."""

from __future__ import annotations

import json
import urllib.error
from unittest.mock import MagicMock, patch

from pydantic import SecretStr

from sap_agent.context import SessionContext
from sap_agent.schemas import Config, QuestionIntent
from sap_agent.tools.llm import (
    _extract_content,
    _parse_llm_json,
    _payload_anthropic,
    _payload_openai,
    call_llm_for_intent,
)


class TestPayloadOpenAI:
    def test_returns_correct_structure(self) -> None:
        payload = _payload_openai("how many orders?", "gpt-5")
        assert payload["model"] == "gpt-5"
        assert payload["temperature"] == 0
        assert payload["response_format"] == {"type": "json_object"}
        assert len(payload["messages"]) == 2
        assert payload["messages"][0]["role"] == "system"
        assert payload["messages"][1]["role"] == "user"
        assert payload["messages"][1]["content"] == "how many orders?"


class TestPayloadAnthropic:
    def test_returns_correct_structure(self) -> None:
        payload = _payload_anthropic("count approved", "claude-3")
        assert payload["model"] == "claude-3"
        assert payload["temperature"] == 0
        assert payload["max_tokens"] == 512
        assert payload["system"] != ""
        assert len(payload["messages"]) == 1
        assert payload["messages"][0]["content"] == "count approved"


class TestExtractContent:
    def test_openai_extracts_message_content(self) -> None:
        resp = {"choices": [{"message": {"content": '{"intent":"count_total"}'}}]}
        assert _extract_content("openai", resp) == '{"intent":"count_total"}'

    def test_openai_empty_choices(self) -> None:
        assert _extract_content("openai", {}) == ""
        assert _extract_content("openai", {"choices": []}) == ""

    def test_openai_missing_content(self) -> None:
        assert _extract_content("openai", {"choices": [{"message": {}}]}) == ""

    def test_anthropic_extracts_text_block(self) -> None:
        resp = {"content": [{"type": "text", "text": '{"intent":"count_where"}'}]}
        assert _extract_content("anthropic", resp) == '{"intent":"count_where"}'

    def test_anthropic_no_text_block(self) -> None:
        resp = {"content": [{"type": "image", "source": "x"}]}
        assert _extract_content("anthropic", resp) == ""

    def test_anthropic_empty_content(self) -> None:
        assert _extract_content("anthropic", {}) == ""


class TestParseLlmJson:
    def test_valid_json_returns_intent_config(self) -> None:
        raw = json.dumps({"intent": "count_total", "column": None})
        result = _parse_llm_json(raw)
        assert result is not None
        assert result.intent == QuestionIntent.COUNT_TOTAL

    def test_strips_markdown_fences(self) -> None:
        raw = '```json\n{"intent": "existence"}\n```'
        result = _parse_llm_json(raw)
        assert result is not None
        assert result.intent == QuestionIntent.EXISTENCE

    def test_strips_bare_fences(self) -> None:
        raw = '```\n{"intent": "count_where", "column": "status"}\n```'
        result = _parse_llm_json(raw)
        assert result is not None
        assert result.intent == QuestionIntent.COUNT_WHERE
        assert result.column == "status"

    def test_invalid_json_returns_none(self) -> None:
        assert _parse_llm_json("not json at all") is None

    def test_unknown_intent_becomes_unsupported(self) -> None:
        raw = json.dumps({"intent": "foo_bar"})
        result = _parse_llm_json(raw)
        assert result is not None
        assert result.intent == QuestionIntent.UNSUPPORTED

    def test_limit_coerced_to_int(self) -> None:
        raw = json.dumps({"intent": "count_total", "limit": "5"})
        result = _parse_llm_json(raw)
        assert result is not None
        assert result.limit == 5

    def test_invalid_limit_becomes_none(self) -> None:
        raw = json.dumps({"intent": "count_total", "limit": "abc"})
        result = _parse_llm_json(raw)
        assert result is not None
        assert result.limit is None

    def test_defaults_filled(self) -> None:
        raw = json.dumps({"intent": "aggregate", "aggregation": "sum"})
        result = _parse_llm_json(raw)
        assert result is not None
        assert result.comparer == "exact"
        assert result.sort_order == "desc"
        assert result.follow_up == ""

    def test_all_fields_populated(self) -> None:
        raw = json.dumps(
            {
                "intent": "aggregate",
                "column": "status",
                "value": "Approved",
                "comparer": "year",
                "aggregation": "avg",
                "aggregation_column": "amount",
                "group_by": "customer",
                "limit": 3,
                "sort_order": "asc",
                "follow_up": "top customers",
            }
        )
        result = _parse_llm_json(raw)
        assert result is not None
        assert result.column == "status"
        assert result.value == "Approved"
        assert result.comparer == "year"
        assert result.aggregation == "avg"
        assert result.aggregation_column == "amount"
        assert result.group_by == "customer"
        assert result.limit == 3
        assert result.sort_order == "asc"
        assert result.follow_up == "top customers"


class TestCallLlmForIntent:
    def _config(self, key: str = "test-key", provider: str = "openai") -> Config:
        return Config(
            app_url="http://x",
            username="u",
            password="p",
            llm_api_key=SecretStr(key),
            llm_provider=provider,
        )

    def test_no_llm_key_returns_none(self) -> None:
        cfg = Config(app_url="http://x", username="u", password="p")
        assert call_llm_for_intent("q", cfg) is None

    def test_empty_llm_key_returns_none(self) -> None:
        cfg = Config(app_url="http://x", username="u", password="p", llm_api_key=SecretStr(""))
        assert call_llm_for_intent("q", cfg) is None

    def test_no_llm_configured_returns_none(self) -> None:
        cfg = Config(app_url="http://x", username="u", password="p")
        assert not cfg.has_llm()
        assert call_llm_for_intent("q", cfg) is None

    @patch("sap_agent.tools.llm._post_json")
    def test_openai_success(self, mock_post: MagicMock) -> None:
        mock_post.return_value = {"choices": [{"message": {"content": json.dumps({"intent": "count_total"})}}]}
        cfg = self._config()
        result = call_llm_for_intent("how many orders?", cfg)
        assert result is not None
        assert result.intent == QuestionIntent.COUNT_TOTAL

    @patch("sap_agent.tools.llm._post_json")
    def test_anthropic_success(self, mock_post: MagicMock) -> None:
        mock_post.return_value = {
            "content": [{"type": "text", "text": json.dumps({"intent": "existence", "column": "status"})}]
        }
        cfg = self._config(provider="anthropic")
        result = call_llm_for_intent("is there any approved order?", cfg)
        assert result is not None
        assert result.intent == QuestionIntent.EXISTENCE

    @patch("sap_agent.tools.llm._post_json")
    def test_empty_content_returns_none(self, mock_post: MagicMock) -> None:
        mock_post.return_value = {"choices": []}
        cfg = self._config()
        assert call_llm_for_intent("q", cfg) is None

    @patch("sap_agent.tools.llm._post_json")
    def test_http_error_returns_none(self, mock_post: MagicMock) -> None:
        mock_post.side_effect = urllib.error.HTTPError(url="http://x", code=429, msg="rate limited", hdrs=None, fp=None)
        cfg = self._config()
        ctx = SessionContext(Config(app_url="http://x", username="u", password="p"))
        result = call_llm_for_intent("q", cfg, ctx)
        assert result is None
        assert any("llm.error" in e.action for e in ctx.trace)

    @patch("sap_agent.tools.llm._post_json")
    def test_generic_exception_returns_none(self, mock_post: MagicMock) -> None:
        mock_post.side_effect = ConnectionError("refused")
        cfg = self._config()
        ctx = SessionContext(Config(app_url="http://x", username="u", password="p"))
        result = call_llm_for_intent("q", cfg, ctx)
        assert result is None
        assert any("llm.error" in e.action for e in ctx.trace)

    @patch("sap_agent.tools.llm._post_json")
    def test_unsupported_llm_result_recorded(self, mock_post: MagicMock) -> None:
        mock_post.return_value = {"choices": [{"message": {"content": json.dumps({"intent": "unsupported"})}}]}
        cfg = self._config()
        ctx = SessionContext(Config(app_url="http://x", username="u", password="p"))
        result = call_llm_for_intent("play jazz", cfg, ctx)
        assert result is not None
        assert result.intent == QuestionIntent.UNSUPPORTED
        assert any("llm.parse" in e.action for e in ctx.trace)

    @patch("sap_agent.tools.llm._post_json")
    def test_openai_url_construction(self, mock_post: MagicMock) -> None:
        mock_post.return_value = {"choices": [{"message": {"content": '{"intent":"count_total"}'}}]}
        cfg = self._config()
        call_llm_for_intent("q", cfg)
        assert mock_post.call_args[0][0].endswith("/chat/completions")

    @patch("sap_agent.tools.llm._post_json")
    def test_anthropic_url_construction(self, mock_post: MagicMock) -> None:
        mock_post.return_value = {"content": [{"type": "text", "text": '{"intent":"count_total"}'}]}
        cfg = self._config(provider="anthropic")
        call_llm_for_intent("q", cfg)
        assert mock_post.call_args[0][0].endswith("/v1/messages")

    @patch("sap_agent.tools.llm._post_json")
    def test_anthropic_custom_base_url(self, mock_post: MagicMock) -> None:
        mock_post.return_value = {"content": [{"type": "text", "text": '{"intent":"count_total"}'}]}
        cfg = self._config(provider="anthropic")
        cfg.llm_base_url = "http://localhost:8080/v1/messages"
        call_llm_for_intent("q", cfg)
        assert mock_post.call_args[0][0] == "http://localhost:8080/v1/messages"

    @patch("sap_agent.tools.llm._post_json")
    def test_openai_custom_base_url(self, mock_post: MagicMock) -> None:
        mock_post.return_value = {"choices": [{"message": {"content": '{"intent":"count_total"}'}}]}
        cfg = self._config()
        cfg.llm_base_url = "http://localhost:8080/chat/completions"
        call_llm_for_intent("q", cfg)
        assert mock_post.call_args[0][0] == "http://localhost:8080/chat/completions"
