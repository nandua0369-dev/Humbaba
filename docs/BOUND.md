# bound — the enforcement engine, without the language

*The runtime doesn't negotiate.*

Humbaba's guarantees do not require Humbaba's syntax. `humbaba.bound` exposes the same
enforcement engine as decorators over ordinary Python.

```bash
python3 examples/bound_demo.py
python3 -m unittest tests/test_bound.py   # 38 tests
```

The name is the idea: authority on a bound, and the bound only ever shortens
as you go down the call stack.

---

## Why this exists

Filter-based guardrails inspect text and guess whether it is an attack. That
guess is adversarially manipulable — the attacker iterates until the filter is
wrong.

Capability enforcement does not guess. If a function never held `db.dump`,
there is nothing to persuade it with. The model can comply with an injection
completely and still change nothing.

This is the difference between *reducing* misuse probability and *removing the
means*.

---

## Declare your capabilities once

```python
from humbaba.bound import declare

declare("model", "db.read", "db.write", "email.send")
```

Names must be declared before use. A typo is then caught at import:

```
@capability('db.wirte'): ['db.wirte'] not declared.
Call declare('db.wirte') first. Known: ['db.read', 'db.write', 'email.send', 'model']
```

Without the registry, `"db.wirte"` would simply be a capability nobody holds —
failing closed, but silently, and surviving review.

---

## The four guarantees

### 1. A function cannot touch what it did not declare

```python
from humbaba.bound import bound, capability

@capability("db.dump")
def db_dump():
    return CUSTOMERS

@bound(uses={"model"})            # note: no db.dump
def summarise(doc):
    reply = call_model(doc)
    return db_dump()                # CapabilityError
```

```
summarise() attempted 'db.dump' but only holds ['model']
```

### 2. Authority only shortens down the call stack

A callee's authority is the *intersection* of what it declares and what its
caller held. Declaring more does not grant more.

```python
@bound(uses={"model", "db.dump"})   # declares it
def inner():
    return db_dump()

@bound(uses={"model"})              # caller never had it
def outer():
    return inner()                    # CapabilityError
```

A function's blast radius is a property of its declaration and its callers —
you can read it off the signature.

### 3. Untrusted input is fenced, and its taint is sticky

```python
from humbaba.bound import Untrusted, fence_all

@bound(uses={"model", "db.write"})
def extract_and_save(doc: Untrusted):
    values, notice = fence_all(document=doc)
    call_model("Extract." + notice, values["document"])
    return db_write(doc)              # TaintError
```

`fence_all` wraps untrusted values in nonce-delimited markers — fresh random
nonce per call, and any attempt to close the fence from inside the payload is
defanged. It returns the sentence to append to your system prompt.

When passing a tainted value really is intended, say so in writing:

```python
db_write(doc.unwrap("reviewed by the operator"))
```

The reason is required, and it lands in the audit log.

### 4. Spend is capped, and charged automatically

```python
from humbaba.bound import metered

@metered(cost=0.0055)
@capability("model")
def call_model(system, user):
    return provider.complete(system, user)

@bound(uses={"model"}, budget=0.02)
def runaway(doc: Untrusted):
    for _ in range(100):
        call_model(...)              # BudgetExceeded on pass 4
```

`@metered` charges the active budget when the call returns, so a provider that
raises costs nothing — matching how providers actually bill. Cost can be flat,
a callable over the result, or a price function registered per model:

```python
from humbaba.bound import set_price

set_price("claude-sonnet", lambda r, *a, **k: r.usage.input_tokens * 3e-6
                                            + r.usage.output_tokens * 15e-6)

@metered(model="claude-sonnet")
@capability("model")
def call_model(...): ...
```

Budgets are parent-linked: a child cannot spend allowance its parent lacks,
however generous the child's own cap.

---

## Async

Everything works on `async def`. Frames live in `contextvars`, so authority
survives an `await`, and concurrent tasks cannot leak into one another.

```python
@bound(uses={"model"})
async def worker(doc: Untrusted):
    await call_model_async(...)      # still holds exactly {"model"}
    db_dump()                        # CapabilityError, after the await

@bound(uses={"model", "db.dump"}, budget=0.10)
async def fleet():
    await asyncio.gather(*(worker(d) for d in docs))
    # parent's own authority is untouched by what its children did
```

The shared budget is enforced across concurrent tasks, so a fan-out cannot
overspend by racing.

---

## Audit

Every decision — enter, exit, spend, allowed, blocked, taint-blocked,
untaint — can be streamed out:

```python
from humbaba.bound import set_audit_sink

set_audit_sink(lambda event, detail: log.info("%s %s", event, detail))
```

A sink that raises cannot break the program; the exception is swallowed by
design. Audit is observation, never a failure path.

---

## API

| Name | Purpose |
|---|---|
| `declare(*names)` | register capability names; required before use |
| `@bound(uses=..., budget=...)` | declare a function's authority and spend cap |
| `@capability("name")` | mark a function as exercising a capability |
| `@metered(cost=... \| model=...)` | charge the active budget automatically |
| `set_price(model, fn)` | register a pricing function for a model |
| `Untrusted` | annotation and wrapper for values from outside |
| `taint(v, origin=...)` / `is_tainted(v)` | wrap explicitly / check |
| `fence(v)` / `fence_all(**kw)` | nonce-delimited fencing for prompts |
| `charge(amount, what)` | charge manually when `@metered` doesn't fit |
| `current_caps()` / `remaining_budget()` | introspection |
| `set_audit_sink(fn)` | stream every decision |
| `declared_capabilities()` | the registered set |
| `set_strict(False)` | disable the registry check (tests only) |

Exceptions: `CapabilityError`, `TaintError`, `BudgetExceeded`,
`UnknownCapability` — all subclass `HumbabaError`.

---

## Limits, stated plainly

- **Taint tracking is dynamic, not static.** The language's `humbaba check`
  catches taint violations before anything runs; bound catches them at the
  moment of the call. Same guarantee, later.
- **Taint follows the wrapper, not the data.** `Untrusted` tracks derivation
  through its own operators. Code that pulls `.value` out, or passes the
  string through a library that rebuilds it, drops the taint. `unwrap()`
  exists so that dropping it is at least deliberate and logged.
- **Capabilities are coarse.** `db.write` is one capability; it does not
  distinguish which table or which row. Finer granularity means more
  capability names, declared by you.
- **Enforcement is in-process.** A subprocess or a separate service is
  outside the bound. Enforce at each boundary, or put the boundary behind a
  capability.
