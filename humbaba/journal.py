# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright 2026 Nandu Aravindakshan

"""Durable execution journal.

Solves docs/LIMITATIONS.md §2.2: an agent that dies at minute nine loses
everything, including money already spent, and repeats side effects on rerun.

Design (docs/ROADMAP.md §1):
  - A run id is derived from the function name and its arguments, so retrying
    the same logical work resumes rather than duplicating.
  - Each `step` appends its result. On restart, steps with a recorded entry
    return that value WITHOUT executing the body; the first step without one
    runs for real.
  - Budget spend is restored, so a resumed run cannot silently spend twice.
  - Each entry stores a hash of the step's identity. If the code changed
    between the crash and the restart, the runtime refuses to resume rather
    than splicing old results into new logic.
"""

import hashlib
import json
import os
import tempfile

from .runtime import Obj, HumbabaError

DEFAULT_DIR = os.path.join(tempfile.gettempdir(), "humbaba-journal")


def _encode(v):
    """Values -> JSON. Records keep their type so they round-trip."""
    if isinstance(v, Obj):
        return {"__obj__": v.type_name,
                "fields": {k: _encode(x) for k, x in v.fields.items()}}
    if isinstance(v, list):
        return [_encode(x) for x in v]
    return v


def _decode(v):
    if isinstance(v, dict) and "__obj__" in v:
        return Obj(v["__obj__"], {k: _decode(x) for k, x in v["fields"].items()})
    if isinstance(v, list):
        return [_decode(x) for x in v]
    return v


class Journal:
    """Append-only record of completed steps for one run."""

    def __init__(self, path, run_id, entries, spent):
        self.path = path
        self.run_id = run_id
        self.entries = entries          # label -> {value, spent, hash}
        self.spent = spent
        self.restored = bool(entries)
        self.completed = len(entries)
        self._replayed = set()

    # ---------------------------------------------------------------- open

    @classmethod
    def open(cls, fn_name, args, directory=None):
        directory = directory or DEFAULT_DIR
        os.makedirs(directory, exist_ok=True)

        try:
            arg_repr = json.dumps([_encode(a) for a in args], sort_keys=True,
                                  default=str)
        except (TypeError, ValueError):
            arg_repr = repr(args)
        run_id = hashlib.sha256(f"{fn_name}\x00{arg_repr}".encode()).hexdigest()[:16]
        path = os.path.join(directory, f"{run_id}.jsonl")

        entries, spent = {}, 0.0
        if os.path.exists(path):
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        # A crash mid-write leaves a torn final line. Everything
                        # before it is still valid, so stop here rather than
                        # discarding the whole journal.
                        break
                    if rec.get("op") == "done":
                        entries, spent = {}, 0.0      # completed; start fresh
                        continue
                    entries[rec["label"]] = rec
                    spent = max(spent, rec.get("spent", 0.0))
        return cls(path, run_id, entries, spent)

    # ---------------------------------------------------------------- use

    def replay(self, label):
        """Return (value,) if this step is already done, else None."""
        rec = self.entries.get(label)
        if rec is None:
            return None
        if label in self._replayed:
            raise HumbabaError(
                f"step {label!r} ran twice in one execution. Step labels must be "
                f"unique within a durable function, or replay is ambiguous."
            )
        self._replayed.add(label)
        return (_decode(rec["value"]),)

    def record(self, label, value, spent):
        rec = {"op": "step", "label": label, "value": _encode(value),
               "spent": round(spent, 6)}
        with open(self.path, "a") as f:
            f.write(json.dumps(rec) + "\n")
            f.flush()
            os.fsync(f.fileno())
        self.entries[label] = rec

    def finish(self):
        """Mark the run complete so a rerun starts fresh rather than resuming."""
        with open(self.path, "a") as f:
            f.write(json.dumps({"op": "done"}) + "\n")

    def discard(self):
        if os.path.exists(self.path):
            os.remove(self.path)
