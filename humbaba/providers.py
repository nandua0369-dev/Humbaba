# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright 2026 Nandu Aravindakshan

"""Real model providers.

Solves docs/LIMITATIONS.md §2.1. Nothing in the runtime knows what a provider
is beyond this signature:

    generate(model, system, user, schema, tool_invoker, notify,
             key_tag, key_material) -> (dict, cost)

so adding one is contained. The important part is not the HTTP call — it is
mapping each provider's failure modes onto Humbaba's hard/soft distinction, because
that decides whether a retry can possibly help:

    HARD (TransientError)  network, 429, 500, 502, 503, timeout
    SOFT (RefusalError)    content filter, truncation, malformed or missing
                           fields, schema violation

VERIFICATION STATUS (2026-08-09), Anthropic adapter:
    VERIFIED   the endpoint is reachable, the URL is correct, and the headers
               (content-type, x-api-key, anthropic-version: 2023-06-01) are
               accepted — a POST with this exact payload returned a well-formed
               401 authentication_error rather than a transport or 404 error.
    VERIFIED   model IDs and pricing, against Anthropic's published docs.
    NOT VERIFIED  the payload shape (tools / tool_choice / input_schema) and
               the response parsing. A control probe showed the API returns 401
               before validating the body — a deliberately malformed payload
               got the identical error — so auth precedes schema checking and
               nothing above proves the request body is correct. That needs a
               real key.

The OpenAI adapter is entirely unverified: api.openai.com is not reachable
from the build environment, and its model IDs have not been rechecked.
"""

import json
import os
import urllib.error
import urllib.request

from .model import TransientError, RefusalError

HARD_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}


def _schema_to_json(schema):
    """Humbaba's field list -> JSON Schema, for constrained decoding."""
    props, required = {}, []
    for name, typ in schema:
        base = getattr(typ, "name", typ)
        if base == "number":
            props[name] = {"type": "number"}
        elif base == "bool":
            props[name] = {"type": "boolean"}
        else:
            props[name] = {"type": "string"}
        if not getattr(typ, "optional", False):
            required.append(name)
    return {"type": "object", "properties": props,
            "required": required, "additionalProperties": False}


def _post(url, payload, headers, timeout):
    body = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode()[:200]
        if e.code in HARD_STATUS:
            raise TransientError(f"HTTP {e.code}: {detail}")
        # 4xx that is not rate limiting is usually a bad request — a retry of
        # the identical call will not help, so it is soft.
        raise RefusalError(f"HTTP {e.code}: {detail}")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise TransientError(f"network: {e}")


def _extract_json(text):
    """Models wrap JSON in prose or fences more often than they should."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                pass
    raise RefusalError("response was not valid JSON")


class Provider:
    """Base class. Subclasses implement _call and _price."""

    name = "provider"
    models = {}
    typical_cost = 0.010

    def __init__(self, api_key=None, timeout=60, models=None):
        self.api_key = api_key or os.environ.get(self.env_key)
        if not self.api_key:
            raise RuntimeError(
                f"{self.name}: no API key. Set {self.env_key} or pass api_key="
            )
        self.timeout = timeout
        if models:
            self.models = dict(self.models, **models)
        self.hits = 0          # kept for CLI-summary compatibility
        self.misses = 0

    def resolve(self, alias):
        """Humbaba programs say "large"/"small"; config maps those to real models."""
        return self.models.get(alias, alias)

    def save(self):
        pass

    def generate(self, model, system, user, schema, tool_invoker=None,
                 notify=None, key_tag=None, key_material=None):
        self.misses += 1
        real_model = self.resolve(model)
        raw, usage = self._call(real_model, system, user, schema)
        value = _extract_json(raw)
        if not isinstance(value, dict):
            raise RefusalError("response JSON was not an object")
        missing = [n for n, t in schema
                   if n not in value and not getattr(t, "optional", False)]
        if missing:
            raise RefusalError(f"missing field(s) {missing}")
        return value, self._price(real_model, usage)

    def _price(self, model, usage):
        rates = self.pricing.get(model)
        if not rates:
            return 0.0
        pin, pout = rates
        return round(usage[0] / 1e6 * pin + usage[1] / 1e6 * pout, 6)


class Anthropic(Provider):
    name = "anthropic"
    env_key = "ANTHROPIC_API_KEY"
    url = "https://api.anthropic.com/v1/messages"
    models = {"large": "claude-sonnet-5", "small": "claude-haiku-4-5-20251001"}
    # USD per million tokens (input, output). Verified against Anthropic's
    # pricing docs on 2026-08-09.
    # NOTE: claude-sonnet-5 is on introductory pricing of 2.00/10.00 until
    # 2026-08-31; standard 3.00/15.00 takes effect 2026-09-01. Update then.
    pricing = {
        "claude-sonnet-5": (2.00, 10.00),
        "claude-haiku-4-5-20251001": (1.00, 5.00),
    }

    def _call(self, model, system, user, schema):
        payload = {
            "model": model,
            "max_tokens": 2048,
            "system": system + "\n\nRespond with a single JSON object matching "
                               "the requested fields. No prose, no code fences.",
            "messages": [{"role": "user", "content": user}],
            "tools": [{
                "name": "emit",
                "description": "Return the extracted fields.",
                "input_schema": _schema_to_json(schema),
            }],
            "tool_choice": {"type": "tool", "name": "emit"},
        }
        data = _post(self.url, payload, {
            "content-type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
        }, self.timeout)

        if data.get("stop_reason") == "max_tokens":
            raise RefusalError("response truncated at max_tokens")

        for block in data.get("content", []):
            if block.get("type") == "tool_use":
                usage = data.get("usage", {})
                return json.dumps(block["input"]), (
                    usage.get("input_tokens", 0), usage.get("output_tokens", 0))
        for block in data.get("content", []):
            if block.get("type") == "text":
                usage = data.get("usage", {})
                return block["text"], (usage.get("input_tokens", 0),
                                       usage.get("output_tokens", 0))
        raise RefusalError("no usable content in response")


class OpenAI(Provider):
    name = "openai"
    env_key = "OPENAI_API_KEY"
    url = "https://api.openai.com/v1/chat/completions"
    models = {"large": "gpt-4o", "small": "gpt-4o-mini"}
    pricing = {"gpt-4o": (2.50, 10.00), "gpt-4o-mini": (0.15, 0.60)}

    def _call(self, model, system, user, schema):
        payload = {
            "model": model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "humbaba_output", "strict": True,
                                "schema": _schema_to_json(schema)},
            },
        }
        data = _post(self.url, payload, {
            "content-type": "application/json",
            "authorization": f"Bearer {self.api_key}",
        }, self.timeout)

        choice = (data.get("choices") or [{}])[0]
        if choice.get("finish_reason") == "length":
            raise RefusalError("response truncated")
        if choice.get("finish_reason") == "content_filter":
            raise RefusalError("blocked by content filter")
        msg = choice.get("message", {})
        if msg.get("refusal"):
            raise RefusalError(f"model refused: {msg['refusal']}")
        usage = data.get("usage", {})
        return msg.get("content", ""), (usage.get("prompt_tokens", 0),
                                        usage.get("completion_tokens", 0))


PROVIDERS = {"anthropic": Anthropic, "openai": OpenAI}


def get(name, **kw):
    if name not in PROVIDERS:
        raise RuntimeError(
            f"unknown provider {name!r}; available: {sorted(PROVIDERS)}"
        )
    return PROVIDERS[name](**kw)
