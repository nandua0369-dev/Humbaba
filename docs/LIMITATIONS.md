# Humbaba — Limitations

Everything currently wrong with, missing from, or unverified in this project,
in one place.

**v0.3 update: most of this document has been resolved.** Sections that are
fixed are kept, struck through, with a note on how — because a limitations
document that quietly deletes its entries is not trustworthy.

---

## Summary

**Humbaba now runs programs.** 57 tests across two suites.

| Category | v0.2 | v0.3 |
|---|---|---|
| Capability enforcement and attenuation | works | works, **now statically checked** |
| Injection fencing | works | works, **plus taint propagation** |
| Writing a real program | **impossible** | **works** — §1 resolved |
| Calling a real model | **impossible** | adapters written, **never run against a live endpoint** |
| Surviving a crash | **impossible** | **works** — journal, §2.2 resolved |
| Budget across `parallel` | **undecided** | **decided and implemented** — §3.1 |
| Static type checking | none | **full pass** — §3.3 |
| Concurrency ceiling | ~10,000 | **~500,000** via `--scheduler asyncio` |
| Go host (HBX) | executes the whole language — capabilities, taint, budgets, policy and durable steps — 16/16 conformance tests; 12/12 conformance tests pass; 12 examples run with no unimplemented opcodes; 3.0 ms execution, 6× slower than the v1 register VM and deliberately not optimised further |
| ~~Go host (v1 IR)~~ | superseded and deleted; it read a format the compiler no longer emits |

### What is still genuinely open

1. ~~**The Go host has never been compiled.**~~ **Closed 2026-08-09.** The HBX host compiles, vets clean, passes 12 conformance tests, and matches the Python front end. Verified on macOS arm64 by the author; still not verifiable in the build container, which has no Go toolchain.
2. **The provider adapters have never made a live call.** No network available.
   The request shapes follow published APIs but are unverified.
3. **Fencing remains mitigation, not proof.** Unchanged, and probably permanent.
4. **`while` has a 10M-iteration guard**, which is a blunt instrument.

Everything else below is resolved.

---

## 1. ~~Blocking — you cannot write a real program~~ RESOLVED in v0.3

All four gaps are closed. `examples/06_modules.hb` exercises every one.

```
capability db.write
type Line  { sku: string  qty: number }
type Order { id: string  line: Line  note: string? }   // nested + optional

fn main() uses { db.write } {
  var total = 0                      // mutable binding
  var i = 0
  while i < len(items) {             // while
    if skip(i) { continue }          // continue
    if total > 500 { break }         // break
    total = total + items[i].qty     // assignment, indexing
    i = i + 1
  }
  if total > 100 and not flagged { print(-total) }   // and / or / not, unary minus
}
```

The original text is kept below for reference.

### 1.1 ~~No assignment~~ — `var` and assignment added

There is no way to rebind a name. `let` binds once per scope; nothing can update
it afterwards.

```
let total = 0
for item in items {
  total = total + item.price    // NOT VALID — no assignment exists
}
```

No counters, no accumulators, no running state. This alone stops most programs.

*Resolved:* `let` is still immutable; `var` creates a mutable binding, and
assignment to a `let` is a compile error. The concurrency tension noted here was
real and is addressed in §3.4 — the checker now rejects writes to an outer
binding from inside `parallel for`.

### 1.2 ~~No boolean operators~~ — `and`, `or`, `not` added, short-circuiting

A condition can be a single comparison and nothing more. There is no `and`, `or`
or `not`.

```
if inv.total > 1000 and inv.vendor == "Acme" { … }    // NOT VALID
```

The workaround is nested `if`, which cannot express `or` at all.

### 1.3 ~~No nested record types~~ — nested records, list types, optional fields, and list indexing added

```
type Order {
  customer: Customer      // REJECTED — records may only hold built-ins
  items:    [LineItem]    // REJECTED — no list types in declarations
}
```

Records may contain only `string`, `number` and `bool`. Real extraction schemas
are nested almost by definition — an invoice has line items, a person has an
address. This is arguably the most damaging single gap for the language's core
use case.

Optional fields are also absent: a missing field is always a soft failure, even
when absence is legitimate.

### 1.4 ~~Loops only over lists~~ — `while`, `break`, `continue` added

No `while`, no `break`, no `continue`. Iteration is bounded and only over a list
that already exists. There is no way to loop until a condition holds, which
rules out most agent control flow — retry-until-satisfied, refine-until-good,
poll-until-ready.

---

## 2. Missing infrastructure

### 2.1 ~~No real provider~~ PARTLY RESOLVED — adapters exist, never run live

There is exactly one provider and it is a mock. It has the right *shape* — cost,
latency, transient failure, refusal, malformed output, record/replay — but it
calls nothing. No program in this repository has ever talked to a model.

**v0.3:** `humbaba/providers.py` implements Anthropic and OpenAI adapters with
schema-constrained decoding, token-based costing, and — the important part —
each provider's failure modes mapped onto Humbaba's hard/soft distinction: 429 and
5xx are hard (retry helps), content filters and truncation are soft (retry
does not). Selected with `--provider anthropic`.

**Still unverified:** the environment this was written in has no network, so
these adapters have **never made a live call**. The pure functions are tested;
the HTTP path is not.

### 2.2 ~~No `durable` / `step`~~ RESOLVED

The headline roadmap feature does not exist. An agent running for ten minutes
that dies at minute nine loses everything, including the money already spent,
and any side effects already committed will be repeated on rerun.

**v0.3:** implemented in `humbaba/journal.py`. A run id derives from the function
name and arguments; each `step` appends its result to an fsync'd journal; on
restart, completed steps replay from the journal without executing, and budget
spend is restored so a resumed run cannot spend twice. A torn final line from a
crash mid-write is tolerated. A completed run is marked done, so a rerun starts
fresh rather than resuming.

The checker enforces the rule that makes replay sound: **side effects and
`gen<>` must live inside a `step`**, or replay would repeat them.

Demo: `examples/07_durable.hb`. Test: `TestJournal`.

### 2.3 ~~No modules or imports~~ RESOLVED

**v0.3:** `import "path.hb"` resolves relative to the importing file, detects
cycles, and raises on duplicate declarations across modules rather than silently
overwriting. Demo: `examples/06_modules.hb` with `examples/lib/invoice.hb`.

### 2.4 The Go host has never been compiled

There is no Go toolchain in the environment where this was built, so `go/` has
**never been compiled by anyone**. It is the single unverified component in an
otherwise fully measured project.

It also was not written in the session that built the rest — see
`docs/PROVENANCE.md`. Its contract is at least *tested* in the sense that the C
VM implements the same IR and passes the suite, but the Go code itself is
unvalidated.

`cd go && make check` is the cheapest way to find out whether it works.

---

## 3. Structural — the design is unfinished, not merely unimplemented

These are harder than the gaps above, because they need a decision rather than
typing.

### 3.1 ~~Budget division across `parallel` is unsolved~~ RESOLVED — fail-fast reservation

Eight concurrent generations race against one shared pot. Whichever charge
arrives after the limit is reached takes the error, so **which iteration fails
is non-deterministic**. Worse, seven may have completed and been paid for while
the eighth fails, leaving a partial result and no clean way to reason about it.

**v0.3 decision: fail-fast on projection.** Before dispatching, the block
projects `iterations × gens-per-iteration × worst-observed-cost` and reserves it
from the budget chain. If it does not fit, the block refuses to start:

```
cannot reserve £0.0600 for parallel for: only £0.0200 remains in main()
— this `parallel for` needs up to £0.0600 for 6 iteration(s). Reduce the
work, raise the budget, or split the block.
```

Unused reservation is refunded when the block completes. The projection is
seeded from the provider's price table so the first block can also be checked.

**Why this one:** a block that refuses to start is far easier to reason about
than one that half-completes and leaves you holding seven paid-for results and
one failure. It is conservative — it will refuse blocks that would have fitted —
and that is the trade being made deliberately.

Test: `TestBudgetReservation`, including that *nothing* is spent on refusal.

### 3.2 ~~Capabilities are a closed set~~ RESOLVED

Only `model`, `web.search` and `db.dump` exist. Users cannot define their own.

**v0.3:** `capability db.write` at the top level declares a new capability. Using
an undeclared one is a compile error that names the missing declaration, so a
library cannot quietly introduce a capability nobody sanctioned.

```
line 1: fn main: undeclared capability 'db.write'.
        Add `capability db.write` at the top level.
```

### 3.3 ~~No static type checking~~ RESOLVED

Everything is checked at runtime. A misspelled prompt argument, a wrong field
name, a bad `gen<T>` type — all surface on the unlucky path, possibly after real
money has been spent.

**v0.3:** `humbaba/check.py` runs before anything executes and catches: unknown
types and fields, wrong arity, argument and return type mismatches, missing or
misnamed prompt arguments, prompts referencing parameters they do not have,
undeclared capabilities, capability attenuation violations, assignment to `let`,
`break`/`continue` outside a loop, `step` outside `durable`, side effects outside
`step`, mutable capture across `parallel`, and taint reaching a capability.

`humbaba check FILE` runs it alone. Return types are now enforced.

### 3.4 ~~Concurrency safety depends on an absence~~ RESOLVED

`parallel for` is safe because Humbaba has no assignment and no way to mutate an
outer binding, so iterations cannot interfere. Shared mutable state across
threads is exactly two things: the budget chain (lock-guarded) and stdout
(lock-guarded).

**v0.3:** the tension was real, and the resolution is a compile-time rule rather
than a runtime lock. Writing to a binding declared outside a `parallel for` is
rejected:

```
cannot assign to 'total' inside `parallel for`: it is declared outside the
block, so iterations would race. Collect results from the block instead.
```

Iteration-local `var` is fine. The safety property is preserved without giving
up assignment.

### 3.5 ~~Recovery is limited to `policy`~~ RESOLVED

There is no `try`, no exception handling, and a failed `gen` terminates the
program. `policy` handles retry and model fallback and nothing else. It cannot
express "if this fails, do something different."

**v0.3:** `try expr` converts a failure into a `Failure` value instead of
terminating. Without `try`, failures still terminate — the default stays loud,
which is deliberate: a second control-flow path that can be silently swallowed
is what makes exceptions unpleasant in production agent code.

---

## 4. Security — narrower than it sounds

Worth stating precisely, because this is the part most worth building on.

### 4.1 What actually holds

**Capability enforcement.** A function may only perform operations its signature
declares, and it can never hold more than its caller. When an injected payload
persuades the model to exfiltrate, the runtime refuses — not because the model
resisted, but because the capability was never granted.

This does not depend on the model behaving, which is what makes it worth
anything.

### 4.2 What does not hold

**Fencing is mitigation, not proof.** Marking a parameter `untrusted` wraps it
in delimiters and tells the model to treat the contents as data. A sufficiently
persuasive payload can still talk a model round. It raises the cost of an
attack; it does not close the class.

The two defences are meant to be worn together: fencing reduces how often
injection succeeds, capabilities bound what happens when it does.

### 4.3 Fixed in v0.2

The fence nonce was originally derived from a content hash so record/replay
stayed deterministic — which made it predictable to whoever supplied the
content, and therefore forgeable. Cassettes are now keyed on the *inputs* rather
than the rendered message, which removed the constraint, so nonces are random
and forged fence openers are defanged before interpolation.

### 4.4 ~~No taint propagation~~ RESOLVED

**v0.3:** taint now propagates through the checker. A `gen<>` whose prompt has
any `untrusted` parameter produces a tainted value; taint spreads through field
access, bindings, arithmetic and function arguments; and a tainted value
reaching a capability call is a compile error:

```
line 6: tainted value passed to 'db.dump'. It derives from `untrusted`
        input; launder it explicitly first.
```

This closes the gap between the two defences. Fencing reduces how often
injection succeeds, capabilities bound what happens when it does, and taint
tracking stops injected content reaching a capability by a route the author did
not notice.

Demo: `examples/08_taint.hb`. Test: `TestTaintPropagation`.

---

## 5. Performance — real, and mostly irrelevant

Fully measured in `docs/PERFORMANCE.md`. The limitations that matter:

| Limitation | Figure | Does it matter? |
|---|---|---|
| Slower than CPython at dispatch | 7.2× | No — 0.0009 % of a model call |
| Cannot run compute in Humbaba | 176,884 ops/s ceiling | Only if you try to; push compute into a tool |
| Concurrency ceiling on threads | ~10,000 in flight | **Fixed** — `--scheduler asyncio` reaches ~500,000 |
| Cold start | 66 ms vs 0.87 ms compiled | Yes for serverless, no for services |

The honest framing: **performance is not this project's problem.** The runtime
is invisible against network latency. Anyone optimising it before the language
can express a program is optimising the wrong thing.

**v0.3:** `--scheduler asyncio` is implemented and engages above 512-wide
blocks. Re-measured carefully (min-of-N, not a single cold run): **1.4–1.5×
faster on throughput**, not the 5.5× an earlier single-run measurement
suggested. The throughput gain is minor and was never the reason.

**The reason is capacity.** Threads wall out near 10,000 in flight at ~2.2 MB of
reserved stack each; asyncio reaches ~500,000 at 0.71 KB. That is the 50×, and
it is what actually lifts the ceiling. No Humbaba program changes, because the
language never exposed `async`/`await`.

---

## 6. Provenance

Part of this repository was not written by the session that built the rest, and
one of its committed benchmark claims was overstated by roughly 1.9× when
re-measured independently.

Full account, including which files and which figures: `docs/PROVENANCE.md`.

---

## 7. What this all means

**Do not:** rely on fencing alone as an injection defence, trust the Go host, or
assume the provider adapters work until one has made a live call.

**Do:** run `humbaba check` on everything, read `examples/08_taint.hb`, and use
`durable` for anything long enough to be worth resuming.

**Next, in order:**

1. `cd go && make check` — still the only unverified component.
2. One live provider call, to validate `humbaba/providers.py`.
3. Write a real agent and find out which of these decisions were wrong.

The list is much shorter than it was. What remains is the part that needs
contact with reality rather than more code.
