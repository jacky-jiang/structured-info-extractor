import json
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from email.utils import parsedate_to_datetime
from typing import Any

import httpx
from pydantic import ValidationError

from .errors import ModelAPIError, ModelResponseError
from .prompts import build_messages
from .schema import EventExtraction
from .usage import TokenUsage

CALENDAR_TOOL = {
    "type": "function",
    "function": {
        "name": "get_calendar_context",
        "description": "Get the trusted reference date used to resolve relative dates.",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
}


@dataclass(frozen=True)
class ExtractionResult:
    data: EventExtraction
    usage: TokenUsage


class ModelClient:
    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        *,
        timeout: float = 45,
        max_retries: int = 3,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.model = model
        self.max_retries = max_retries
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "ModelClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def extract(
        self,
        text: str,
        *,
        reference_date: date,
        timezone: str = "local",
        on_delta: Callable[[str], None] | None = None,
        use_tools: bool = False,
    ) -> ExtractionResult:
        messages = build_messages(text, reference_date, timezone)
        if use_tools:
            if on_delta:
                raise ModelResponseError("This practice keeps tool calling non-streaming; disable --stream.")
            return self._extract_with_tools(messages, reference_date, timezone)
        payload = self._payload(messages, stream=on_delta is not None)
        if on_delta:
            return self._stream_request(payload, on_delta)
        return self._parse_response(self._post_with_retry(payload))

    def _payload(self, messages: list[dict], *, stream: bool = False, tools: bool = False) -> dict:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "event_extraction",
                    "strict": True,
                    "schema": EventExtraction.model_json_schema(),
                },
            },
        }
        if stream:
            payload["stream"] = True
            payload["stream_options"] = {"include_usage": True}
        if tools:
            payload["tools"] = [CALENDAR_TOOL]
            payload["tool_choice"] = "auto"
        return payload

    def _post_with_retry(self, payload: dict) -> dict:
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = self._client.post("/chat/completions", json=payload)
                if response.status_code in {408, 409, 429} or response.status_code >= 500:
                    if attempt < self.max_retries:
                        time.sleep(self._retry_delay(response, attempt))
                        continue
                if response.is_error:
                    raise ModelAPIError(f"Model API HTTP {response.status_code}: {response.text[:500]}")
                return response.json()
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_error = exc
                if attempt < self.max_retries:
                    time.sleep(0.5 * (2**attempt) + random.random() * 0.1)
                    continue
            except json.JSONDecodeError as exc:
                raise ModelResponseError("The API response was not valid JSON.") from exc
        raise ModelAPIError(f"Model request failed after retries: {last_error or 'retryable HTTP error'}")

    @staticmethod
    def _retry_delay(response: httpx.Response, attempt: int) -> float:
        value = response.headers.get("Retry-After")
        if value:
            try:
                return min(float(value), 30)
            except ValueError:
                try:
                    seconds = (parsedate_to_datetime(value) - parsedate_to_datetime(response.headers["Date"])).total_seconds()
                    return max(0, min(seconds, 30))
                except (KeyError, TypeError, ValueError):
                    pass
        return min(0.5 * (2**attempt) + random.random() * 0.1, 8)

    def _stream_request(self, payload: dict, on_delta: Callable[[str], None]) -> ExtractionResult:
        parts: list[str] = []
        usage = TokenUsage()
        try:
            with self._client.stream("POST", "/chat/completions", json=payload) as response:
                if response.is_error:
                    response.read()
                    raise ModelAPIError(f"Model API HTTP {response.status_code}: {response.text[:500]}")
                for line in response.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        event = json.loads(data)
                    except json.JSONDecodeError as exc:
                        raise ModelResponseError("Invalid JSON in streaming event.") from exc
                    if event.get("usage"):
                        usage = TokenUsage.from_api(event["usage"])
                    for choice in event.get("choices", []):
                        delta = choice.get("delta", {}).get("content")
                        if isinstance(delta, str):
                            parts.append(delta)
                            on_delta(delta)
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise ModelAPIError(f"Streaming request failed: {exc}") from exc
        return self._parse_content("".join(parts), usage)

    def _extract_with_tools(self, messages: list[dict], reference_date: date, timezone: str) -> ExtractionResult:
        latest_usage = TokenUsage()
        for _ in range(5):
            data = self._post_with_retry(self._payload(messages, tools=True))
            latest_usage = TokenUsage.from_api(data.get("usage"))
            try:
                message = data["choices"][0]["message"]
            except (KeyError, IndexError, TypeError) as exc:
                raise ModelResponseError("Model response has no choices[0].message.") from exc
            calls = message.get("tool_calls") or []
            if not calls:
                return self._parse_response(data)
            messages.append(message)
            for call in calls:
                function = call.get("function", {})
                name = function.get("name")
                if name != "get_calendar_context":
                    raise ModelResponseError(f"Model requested an unregistered tool: {name}")
                try:
                    arguments = json.loads(function.get("arguments") or "{}")
                except json.JSONDecodeError as exc:
                    raise ModelResponseError("Tool arguments were not valid JSON.") from exc
                if arguments:
                    raise ModelResponseError("get_calendar_context takes no arguments.")
                result = {
                    "reference_date": reference_date.isoformat(),
                    "weekday": reference_date.strftime("%A"),
                    "timezone": timezone,
                }
                messages.append({
                    "role": "tool",
                    "tool_call_id": call.get("id"),
                    "content": json.dumps(result, ensure_ascii=False),
                })
        raise ModelResponseError("Tool-calling loop exceeded 5 model requests.")

    def _parse_response(self, data: dict) -> ExtractionResult:
        usage = TokenUsage.from_api(data.get("usage"))
        try:
            choice = data["choices"][0]
            if choice.get("finish_reason") == "length":
                raise ModelResponseError("Output was truncated by the model token limit.")
            message = choice["message"]
            if message.get("refusal"):
                raise ModelResponseError(f"Model refused: {message['refusal']}")
            content = message["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ModelResponseError("Unexpected model response structure.") from exc
        if isinstance(content, list):
            content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
        if not isinstance(content, str):
            raise ModelResponseError("Model returned no text content.")
        return self._parse_content(content, usage)

    @staticmethod
    def _parse_content(content: str, usage: TokenUsage) -> ExtractionResult:
        try:
            raw = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ModelResponseError("Model content was not valid JSON.") from exc
        try:
            return ExtractionResult(data=EventExtraction.model_validate(raw), usage=usage)
        except ValidationError as exc:
            raise ModelResponseError(f"JSON failed business schema validation: {exc}") from exc
