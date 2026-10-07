"""The single path to the model. Every call is logged to intake.llm_call.

Output is constrained with structured outputs (JSON schema). If the response
still fails our own validation, the call is retried with the error appended,
and each attempt is a separate logged row.
"""

import hashlib
import json
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import anthropic
from sqlalchemy import text

from sog.config import settings
from sog.db import engine

# USD per million tokens: (input, output, cache read, cache write 5m)
PRICES = {
    "claude-opus-5-5": (4.0, 20.0, 0.40, 5.0),
    "claude-sonnet-5-5": (2.0, 10.0, 0.20, 2.5),
    "claude-haiku-4-5": (1.0, 5.0, 0.10, 1.25),
}

_client: anthropic.Anthropic | None = None


def client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        if not settings.anthropic_api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set in .env")
        _client = anthropic.Anthropic(api_key=settings.anthropic_api_key, base_url=settings.sog_anthropic_base_url,
                                      max_retries=3, timeout=300)
    return _client


# Haiku 4.5 rejects the effort parameter; the 4.6+ models accept it.
NO_EFFORT = ("claude-haiku-4-5",)


def output_config(model: str, effort: str, schema: dict) -> dict:
    config: dict = {"format": {"type": "json_schema", "schema": schema}}
    if not model.startswith(NO_EFFORT):
        config["effort"] = effort
    return config


class ValidationFailed(Exception):
    pass


@dataclass
class Result:
    data: dict
    call_id: int
    attempts: int


def cost(model: str, usage: Any) -> float | None:
    price = PRICES.get(model)
    if not price:
        return None
    return round((usage.input_tokens * price[0] + usage.output_tokens * price[1]
                  + (usage.cache_read_input_tokens or 0) * price[2]
                  + (usage.cache_creation_input_tokens or 0) * price[3]) / 1e6, 6)


def structured_call(*, run_id: int | None, trace_id: uuid.UUID, model: str, prompt_version: str, system: str,
                    content: list[dict], schema: dict, effort: str = "medium", max_tokens: int = 32000,
                    validate: Callable[[dict], None] | None = None, max_attempts: int = 2) -> Result:
    messages = [{"role": "user", "content": content}]
    request_hash = hashlib.sha256(
        json.dumps({"m": model, "s": system, "c": content, "schema": schema}, sort_keys=True).encode()).hexdigest()
    last_error = ""
    for attempt in range(1, max_attempts + 1):
        started = time.monotonic()
        row: dict[str, Any] = dict(run=run_id, trace=str(trace_id), attempt=attempt, model=model, pv=prompt_version,
                                   hash=request_hash)
        try:
            with client().messages.stream(
                model=model, max_tokens=max_tokens,
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                messages=messages,
                output_config=output_config(model, effort, schema),
            ) as stream:
                response = stream.get_final_message()
        except anthropic.APIError as e:
            log(row | dict(latency=int((time.monotonic() - started) * 1000), error=f"{type(e).__name__}: {e}"))
            raise
        usage = response.usage
        body = next((b.text for b in response.content if b.type == "text"), "")
        row |= dict(latency=int((time.monotonic() - started) * 1000), tin=usage.input_tokens, tout=usage.output_tokens,
                    cr=usage.cache_read_input_tokens, cw=usage.cache_creation_input_tokens,
                    cost=cost(model, usage), stop=response.stop_reason)
        try:
            if response.stop_reason != "end_turn":
                raise ValidationFailed(f"stop_reason={response.stop_reason}")
            data = json.loads(body)
            if validate:
                validate(data)
        except (json.JSONDecodeError, ValidationFailed) as e:
            last_error = str(e)
            log(row | dict(response=body[:200000], validation_error=last_error))
            messages = [{"role": "user", "content": content + [
                {"type": "text", "text": f"Your previous answer was rejected: {last_error}. Answer again, fixing that."}]}]
            continue
        call_id = log(row | dict(response=body))
        return Result(data, call_id, attempt)
    raise ValidationFailed(f"no valid response after {max_attempts} attempts: {last_error}")


def log(row: dict) -> int:
    with engine.begin() as conn:
        return conn.execute(
            text("""INSERT INTO intake.llm_call (run_id, trace_id, attempt, model, prompt_version, request_hash,
                    input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, latency_ms, cost_usd,
                    stop_reason, response, validation_error, error)
                    VALUES (:run, :trace, :attempt, :model, :pv, :hash, :tin, :tout, :cr, :cw, :latency, :cost,
                            :stop, CAST(:response AS jsonb), :validation_error, :error) RETURNING call_id"""),
            {"tin": None, "tout": None, "cr": None, "cw": None, "latency": None, "cost": None, "stop": None,
             "validation_error": None, "error": None, **row,
             "response": json.dumps({"text": row["response"]}) if row.get("response") is not None else None},
        ).scalar_one()
