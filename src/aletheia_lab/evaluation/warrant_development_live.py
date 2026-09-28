"""Explicitly authorized paid development caller; never used by offline preparation."""

from __future__ import annotations

import importlib
import json
import os
import threading
import time
from typing import Any

import tiktoken

from aletheia_lab.evaluation.warrant_development import DevelopmentCall
from aletheia_lab.evaluation.warrant_development_io import (
    DESTINATION,
    MAX_CALL_USD,
    MAX_INPUT_TOKENS,
    MAX_OUTPUT_TOKENS,
    MODEL,
)
from aletheia_lab.model_gateway.openai import _openai_response_format


class OpenAIDevelopmentCaller:
    """Fixed destination/snapshot, bounded input, zero SDK retries, conservative billing.

    A network exception may occur after charging. Unknown usage is reserved at the
    maximum, rather than assumed free. No exception message or key is persisted.
    """

    def __init__(self, *, maximum_calls: int, client: Any = None) -> None:
        if type(maximum_calls) is not int or not 1 <= maximum_calls <= 1809:
            raise ValueError("paid development call bound must be in 1..1809")
        self.maximum_calls = maximum_calls
        self._count = 0
        self._lock = threading.Lock()
        self._encoding = tiktoken.get_encoding("o200k_base")
        if client is None:
            if any(
                os.environ.get(name)
                for name in (
                    "OPENAI_BASE_URL",
                    "OPENAI_ORG_ID",
                    "OPENAI_ORGANIZATION",
                    "OPENAI_PROJECT_ID",
                    "OPENAI_PROJECT",
                    "HTTP_PROXY",
                    "HTTPS_PROXY",
                    "ALL_PROXY",
                )
            ):
                raise ValueError(
                    "redirect, proxy, organization or project overrides are not authorized"
                )
            key = os.environ.get("OPENAI_API_KEY", "")
            if not key.strip():
                raise ValueError("API credential is absent")
            module = importlib.import_module("openai")
            httpx = importlib.import_module("httpx")
            client = module.OpenAI(
                api_key=key,
                base_url=DESTINATION.removesuffix("/chat/completions"),
                max_retries=0,
                timeout=90.0,
                http_client=httpx.Client(trust_env=False, timeout=90.0),
            )
        self._client = client

    def invoke(
        self, *, prompt: str, payload: dict[str, object], schema: dict[str, object]
    ) -> DevelopmentCall:
        text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        response_format = _openai_response_format(json.dumps(schema))
        input_bound = (
            len(
                self._encoding.encode(
                    prompt + text + json.dumps(response_format), disallowed_special=()
                )
            )
            + 512
        )
        if input_bound > MAX_INPUT_TOKENS:
            return DevelopmentCall(
                status="technical_failure",
                payload_json=None,
                input_tokens=0,
                output_tokens=0,
                estimated_cost_usd=0.0,
                latency_seconds=0.0,
                usage_observed=False,
                provider_attempted=False,
            )
        with self._lock:
            if self._count >= self.maximum_calls:
                raise ValueError("approved provider call ceiling exhausted")
            self._count += 1
        started = time.monotonic()
        try:
            response = self._client.chat.completions.create(
                model=MODEL,
                messages=[{"role": "system", "content": prompt}, {"role": "user", "content": text}],
                response_format=response_format,
                temperature=0.0,
                seed=731,
                max_tokens=MAX_OUTPUT_TOKENS,
                store=False,
            )
        except Exception:
            return self._unknown_call(started)
        usage = response.usage
        if (
            usage is None
            or usage.prompt_tokens > MAX_INPUT_TOKENS
            or usage.completion_tokens > MAX_OUTPUT_TOKENS
        ):
            return self._unknown_call(started)
        choice = response.choices[0] if response.choices else None
        content = choice.message.content if choice is not None else None
        completed = (
            choice is not None
            and choice.finish_reason == "stop"
            and not choice.message.refusal
            and isinstance(content, str)
        )
        return DevelopmentCall(
            status="completed" if completed else "technical_failure",
            payload_json=content if isinstance(content, str) else None,
            input_tokens=usage.prompt_tokens,
            output_tokens=usage.completion_tokens,
            estimated_cost_usd=(usage.prompt_tokens * 2 + usage.completion_tokens * 8) / 1_000_000,
            latency_seconds=time.monotonic() - started,
            usage_observed=True,
        )

    @staticmethod
    def _unknown_call(started: float) -> DevelopmentCall:
        return DevelopmentCall(
            status="technical_failure",
            payload_json=None,
            input_tokens=MAX_INPUT_TOKENS,
            output_tokens=MAX_OUTPUT_TOKENS,
            estimated_cost_usd=MAX_CALL_USD,
            latency_seconds=time.monotonic() - started,
            usage_observed=False,
        )
