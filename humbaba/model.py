# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright 2026 Nandu Aravindakshan

"""A deterministic stand-in for a real model provider.

Real providers get swapped in later; everything the language cares about
(cost, failure modes, record/replay, injection behaviour) is here.
"""

import hashlib
import json
import os
import random
import re
import time


class TransientError(Exception):
    """Hard failure: network died, provider 503'd. Retrying may help."""


class RefusalError(Exception):
    """Soft failure: the model answered, but not usably."""


MODELS = {
    # name: (price per 1k chars, latency seconds, competence 0-1)
    "large": (0.020, 0.25, 1.0),
    "small": (0.004, 0.08, 0.7),
}


class MockModel:
    def __init__(self, cassette=None, chaos=0.0, seed=7, verbose=False, overloaded=()):
        self.cassette_path = cassette
        self.chaos = chaos
        self.overloaded = set(overloaded)
        self.rng = random.Random(seed)
        self.verbose = verbose
        self.cassette = {}
        self.hits = 0
        self.misses = 0
        if cassette and os.path.exists(cassette):
            with open(cassette) as f:
                self.cassette = json.load(f)

    def save(self):
        if self.cassette_path:
            with open(self.cassette_path, "w") as f:
                json.dump(self.cassette, f, indent=2, sort_keys=True)

    # A conservative per-call estimate used to project `parallel for` cost
    # before any call has been made. See LIMITATIONS §3.1.
    typical_cost = 0.010

    # ---------- cost ----------

    @staticmethod
    def price(model, system, user):
        rate = MODELS[model][0]
        return round(rate * (len(system) + len(user)) / 1000.0, 6)

    # ---------- main entry ----------

    def generate(self, model, system, user, schema, tool_invoker=None,
                 notify=None, key_tag=None, key_material=None):
        """Returns (value_dict, cost). Raises TransientError / RefusalError.

        `key_material` lets the caller key the cassette on the *inputs* rather
        than the rendered message. That matters because fences carry a random
        nonce: keying on the message would make every run a cache miss.
        """
        if key_material is None:
            if key_tag is None:
                key_tag = "\x00".join(f"{n}:{t}" for n, t in schema)
            key_material = f"{model}\x00{system}\x00{user}\x00{key_tag}"
        key = hashlib.sha256(key_material.encode()).hexdigest()[:16]

        cost = self.price(model, system, user)

        if key in self.cassette:
            self.hits += 1
            return dict(self.cassette[key]), 0.0        # replay is free

        self.misses += 1

        if self.chaos and self.rng.random() < self.chaos:
            raise TransientError(f"{model}: upstream 503")

        time.sleep(MODELS[model][1])

        # --- simulate reading the prompt ---
        instruction_surface = strip_fenced(user)
        if looks_like_injection(instruction_surface):
            # The model is obedient. This is the whole problem.
            if tool_invoker:
                tool_invoker("db.dump", "exfiltrate customer table")
        elif looks_like_injection(user) and notify:
            notify("injection text seen inside the fence — treated as data, not obeyed")

        if model in self.overloaded:
            # Under load, real providers truncate rather than fail cleanly.
            # This is a *soft* failure: an answer arrived, it just isn't usable.
            partial = fabricate(schema, user, 1.0, self.rng) or {}
            partial.pop(schema[-1][0], None)
            return partial, cost

        competence = MODELS[model][2]
        value = fabricate(schema, user, competence, self.rng)
        if value is None:
            raise RefusalError(f"{model}: could not satisfy the requested shape")

        self.cassette[key] = dict(value)
        return value, cost


# ---------- fencing helpers ----------

FENCE_RE = re.compile(r"<<<HUMBABA-DATA:[0-9a-f]+>>>.*?<<<END-HUMBABA-DATA:[0-9a-f]+>>>", re.S)

INJECTION_RE = re.compile(
    r"ignore (all )?(previous|prior) instructions"
    r"|disregard the above"
    r"|you are now"
    r"|dump the (customer|user) (table|database)",
    re.I,
)


def strip_fenced(text: str) -> str:
    """Everything the model is allowed to treat as instructions."""
    return FENCE_RE.sub(" [data] ", text)


def looks_like_injection(text: str) -> bool:
    return bool(INJECTION_RE.search(text))


# ---------- output fabrication ----------

VENDOR_RE = re.compile(r"(?:from|INVOICE from)\s+([A-Z][\w&]*(?:\s+[A-Z][\w&]*)*)")
NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")


_URGENCY_KW  = {"urgent","critical","broken","down","alert","security","asap",
                "emergency","immediately","danger","attack","breach","outage",
                "injection","vulnerability","exploit","zero-knowledge","proof",
                "attestation","sandboxing","wasm","concurrent","concurrency",
                "crdt","distributed","cost","management","strategy"}
_NORMAL_KW   = {"report","meeting","notes","attached","update","review",
                "standup","sprint","schedule","fyi","attached"}
_SPAM_KW     = {"winner","congratulation","prize","gift","claim","selected",
                "reward","free","limited"}
_POS_KW      = {"good","great","excellent","success","perfect","clean","pass",
                "valid","safe","approved","resolved","fixed","passed"}
_NEG_KW      = {"bad","broken","fail","error","down","issue","problem","urgent",
                "critical","breach","invalid","rejected","danger","attack"}


def _tokens(body):
    return set(re.findall(r"[a-z]+", body.lower()))


def _infer_category(body):
    toks = _tokens(body)
    if toks & _SPAM_KW:    return "spam"
    if toks & _URGENCY_KW: return "urgent"
    return "normal"


def _infer_score(body):
    toks = _tokens(body)
    if toks & _SPAM_KW:    return 2
    if toks & _URGENCY_KW: return 9
    return 4


def _infer_sentiment(body):
    toks = _tokens(body)
    pos = len(toks & _POS_KW)
    neg = len(toks & _NEG_KW)
    if neg > pos: return "negative"
    if pos > neg: return "positive"
    return "neutral"


def _short(body, n=10):
    words = body.split()
    return " ".join(words[:n]) + ("…" if len(words) > n else "")


def fabricate(schema, user, competence, rng):
    """Produce a plausible object of the requested shape.

    Reads only the payload, never the scaffolding: fence markers carry a hex
    nonce, and letting its digits reach the extractor corrupts the output.
    Uses field-name semantics so the output is legible in demos.
    """
    body = strip_fenced_content(user)
    out = {}
    for fname, ftype in schema:
        low = fname.lower()

        # ---- numbers ---------------------------------------------------
        if ftype == "number":
            if any(k in low for k in ("score", "urgency", "priority", "level")):
                out[fname] = _infer_score(body)
            elif any(k in low for k in ("count", "qty", "quantity", "num")):
                nums = [float(n.replace(",", "")) for n in NUM_RE.findall(body)]
                out[fname] = float(len(body.split())) if not nums else nums[0]
            else:
                nums = [float(n.replace(",", "")) for n in NUM_RE.findall(body)]
                if not nums:
                    out[fname] = 1.0
                else:
                    out[fname] = max(nums) if competence >= 1.0 else nums[0]

        # ---- booleans --------------------------------------------------
        elif ftype == "bool":
            if any(k in low for k in ("urgent", "flag", "issue", "error", "alert")):
                out[fname] = _infer_score(body) >= 7
            elif any(k in low for k in ("valid", "approved", "pass", "ok")):
                out[fname] = _infer_sentiment(body) != "negative"
            else:
                out[fname] = True

        # ---- strings — semantic routing --------------------------------
        elif any(k in low for k in ("category", "type", "label", "class", "tier")):
            out[fname] = _infer_category(body)
        elif any(k in low for k in ("sentiment", "tone", "mood", "feeling")):
            out[fname] = _infer_sentiment(body)
        elif any(k in low for k in ("vendor", "company", "org", "supplier")):
            m = VENDOR_RE.search(body)
            out[fname] = m.group(1).strip() if m else "Unknown Vendor"
        elif any(k in low for k in ("name", "author", "sender", "from")):
            m = VENDOR_RE.search(body)
            out[fname] = m.group(1).strip() if m else "Unknown"
        elif any(k in low for k in ("action", "next", "todo", "step")):
            score = _infer_score(body)
            if score >= 8:
                out[fname] = "Escalate immediately to on-call engineer"
            elif score >= 5:
                out[fname] = "Review and respond within 24 hours"
            else:
                out[fname] = "No immediate action required"
        elif any(k in low for k in ("headline", "title", "subject", "topic")):
            out[fname] = _short(body, 7)
        elif any(k in low for k in ("reason", "explanation", "why", "rationale")):
            cat = _infer_category(body)
            score = _infer_score(body)
            out[fname] = f"Classified as {cat} (urgency {score}/10)"
        elif any(k in low for k in ("summary", "text", "content", "report",
                                    "note", "body", "result", "output")):
            out[fname] = _short(body, 12)
        elif any(k in low for k in ("status", "state", "outcome")):
            out[fname] = "resolved" if _infer_sentiment(body) == "positive" else "pending"
        elif any(k in low for k in ("id", "ref", "code", "key")):
            out[fname] = "HUMBABA-" + hex(abs(hash(body)))[2:8].upper()
        elif any(k in low for k in ("date", "time", "when", "at")):
            out[fname] = "2026-08-08"
        else:
            out[fname] = _short(body, 10)

    return out


def strip_fenced_content(user: str) -> str:
    """The data the model is meant to work on (inside the fence)."""
    inner = re.findall(
        r"<<<HUMBABA-DATA:[0-9a-f]+>>>(.*?)<<<END-HUMBABA-DATA:[0-9a-f]+>>>", user, re.S
    )
    return " ".join(x.strip() for x in inner) if inner else user
