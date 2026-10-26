import json
import unittest
from datetime import date
from decimal import Decimal
from unittest.mock import patch

import httpx
from pydantic import ValidationError

from event_extractor.client import ModelClient
from event_extractor.errors import ModelAPIError, ModelResponseError
from event_extractor.schema import EventExtraction
from event_extractor.usage import CostRates, TokenUsage

TEXT = "客户希望下周一下午预约产品演示，参加人有张三和李四。"
EXPECTED = {
    "event_type": "product_demo",
    "date": "2026-07-20",
    "period": "afternoon",
    "attendees": ["张三", "李四"],
}


def response(content=EXPECTED, usage=None):
    return {
        "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": json.dumps(content, ensure_ascii=False)}}],
        "usage": usage or {"prompt_tokens": 100, "completion_tokens": 30, "total_tokens": 130},
    }


def make_client(handler):
    return ModelClient(
        "test-key", "https://example.test/v1", "test-model",
        transport=httpx.MockTransport(handler), max_retries=1,
    )


class ExtractorTests(unittest.TestCase):
    def test_structured_extraction_and_usage(self):
        seen = {}
        def handler(request):
            seen.update(json.loads(request.content))
            return httpx.Response(200, json=response())
        with make_client(handler) as client:
            result = client.extract(TEXT, reference_date=date(2026, 7, 13))
        self.assertEqual(result.data.model_dump(), EXPECTED)
        self.assertEqual(result.usage.total_tokens, 130)
        self.assertIs(seen["response_format"]["json_schema"]["strict"], True)
        self.assertEqual([m["role"] for m in seen["messages"]], ["system", "user"])
        self.assertIn("2026-07-13", seen["messages"][0]["content"])

    def test_streaming_collects_json_and_usage(self):
        content = json.dumps(EXPECTED, ensure_ascii=False)
        midpoint = len(content) // 2
        events = [
            {"choices": [{"delta": {"content": content[:midpoint]}}]},
            {"choices": [{"delta": {"content": content[midpoint:]}}]},
            {"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}},
        ]
        body = "".join(f"data: {json.dumps(e, ensure_ascii=False)}\n\n" for e in events) + "data: [DONE]\n\n"
        def handler(request):
            self.assertEqual(json.loads(request.content)["stream_options"], {"include_usage": True})
            return httpx.Response(200, text=body, headers={"Content-Type": "text/event-stream"})
        deltas = []
        with make_client(handler) as client:
            result = client.extract(TEXT, reference_date=date(2026, 7, 13), on_delta=deltas.append)
        self.assertEqual("".join(deltas), content)
        self.assertEqual(result.data.model_dump(), EXPECTED)
        self.assertEqual(result.usage.total_tokens, 15)

    def test_tool_calling_loop(self):
        requests = []
        def handler(request):
            payload = json.loads(request.content)
            requests.append(payload)
            if len(requests) == 1:
                return httpx.Response(200, json={
                    "choices": [{"message": {
                        "role": "assistant", "content": None,
                        "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "get_calendar_context", "arguments": "{}"}}],
                    }}],
                    "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
                })
            self.assertEqual(requests[-1]["messages"][-1]["role"], "tool")
            self.assertIn("2026-07-13", requests[-1]["messages"][-1]["content"])
            return httpx.Response(200, json=response())
        with make_client(handler) as client:
            result = client.extract(TEXT, reference_date=date(2026, 7, 13), use_tools=True)
        self.assertEqual(result.data.date, "2026-07-20")
        self.assertEqual(len(requests), 2)

    @patch("event_extractor.client.time.sleep", lambda _: None)
    def test_retries_rate_limit(self):
        attempts = 0
        def handler(request):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                return httpx.Response(429, json={"error": "rate limited"})
            return httpx.Response(200, json=response())
        with make_client(handler) as client:
            client.extract(TEXT, reference_date=date(2026, 7, 13))
        self.assertEqual(attempts, 2)

    @patch("event_extractor.client.time.sleep", lambda _: None)
    def test_api_error_is_not_retried_forever(self):
        with make_client(lambda request: httpx.Response(500, text="temporary")) as client:
            with self.assertRaisesRegex(ModelAPIError, "HTTP 500"):
                client.extract(TEXT, reference_date=date(2026, 7, 13))

    def test_business_schema_rejects_impossible_date(self):
        with self.assertRaises(ValidationError):
            EventExtraction(event_type="product_demo", date="2026-02-30", period="afternoon", attendees=[])

    def test_invalid_model_json_is_distinguished_from_http_error(self):
        with make_client(lambda request: httpx.Response(200, json={"choices": [{"message": {"content": "not-json"}}]})) as client:
            with self.assertRaisesRegex(ModelResponseError, "not valid JSON"):
                client.extract(TEXT, reference_date=date(2026, 7, 13))

    def test_cost_uses_configured_rates_not_hardcoded_prices(self):
        usage = TokenUsage(1_000_000, 500_000, 1_500_000, True)
        self.assertEqual(CostRates(Decimal("2"), Decimal("8")).calculate(usage), Decimal("6"))


if __name__ == "__main__":
    unittest.main()
