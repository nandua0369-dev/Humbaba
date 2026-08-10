# Humbaba — Roadmap

What is missing, why it is missing, and how it would be built. For the same
gaps stated as *limitations* rather than as plans — ordered by what blocks you
today — see [LIMITATIONS.md](LIMITATIONS.md). Ordered by what I
would actually do next, not by ambition.

Each item states the **problem**, the **design**, and the **risk** — because on
several of these the risk is the interesting part.

---

## 1. `durable` / `step` — crash recovery (v0.2)

*The largest missing feature and the strongest reason anyone would switch.*

### Problem

An agent runs for ten minutes across twelve model calls. The process dies at
minute nine. Today, everything is lost — including the £4 already spent — and
worse, any side effects already committed will be repeated when someone reruns
it.

### Design

```
durable fn onboard(customer: Customer) -> Outcome {
  let profile = step { enrich(customer) }
  let review  = step { gen<Review> from assess(p: profile) }
  step { crm.create(profile, review) }
}
```

Each `durable` invocation gets a **run id**, derived from a hash of the function
name and its arguments, so a retry of the same logical work resumes rather than
duplicating.

A journal is appended to on every step boundary:

```json
{"run": "a91f…", "seq": 3, "op": "step",
 "hash": "sha256 of the step's source",
 "value": {"vendor": "Acme", "total": 2400.0},
 "spent": 0.0121, "ts": 1753500000.0}
```

On restart the runtime replays the journal: for each `step` in order, if a
matching entry exists, its recorded value is returned **without executing the
body**; the first step with no entry is executed for real, and execution
proceeds normally from there. Budget spend is restored from the journal so a
resumed run cannot silently spend twice.

Two rules make this sound, and both need compiler enforcement:

1. **Side effects only inside `step`.** A capability call outside a `step` in a
   `durable` function is a compile error, because replay would repeat it.
2. **`step` bodies must be deterministic given their inputs.** `gen` is
   explicitly *not* deterministic, which is exactly why its result is journaled
   rather than recomputed.

The `hash` field detects the nastiest failure mode: someone edits the function
between the crash and the restart. If the recorded hash for a step no longer
matches its source, the runtime refuses to resume and says so, rather than
splicing old results into new logic.

### Risk

Replay semantics confuse experienced engineers routinely — this is the most
common complaint about Temporal, which is the mature version of this idea. The
feature lives or dies on error messages. "Step 3 was recorded when the code
looked different; refusing to resume run a91f" is the difference between a
useful feature and one people disable.

---

## 2. Hardening the injection defence (v0.2)

### Problem

Two known weaknesses in §7.2 of the language reference:

- The fence nonce is derived from a content hash, so it is predictable to anyone
  who knows the content. A payload could in principle close the fence early and
  escape.
- The defence is text-level. It raises attack cost; it does not eliminate the
  class.

### Design

- ~~**Random nonces.**~~ **Done in v0.2.** Cassettes are now keyed on the
  *inputs* rather than the rendered message, which removed the reason the nonce
  had to be deterministic. It is now `secrets.token_hex(4)`. This came out of
  the optimisation pass — keying on inputs was also faster.
- ~~**Reject forged markers.**~~ **Done in v0.2.** Untrusted content has
  anything resembling a fence opener defanged before interpolation.
- **Taint tracking.** Today `untrusted` applies at the prompt boundary only. It
  should propagate: a value derived from a model's output that was itself
  derived from untrusted input is still untrusted, and passing it to a
  capability call should be a compile-time error unless explicitly laundered.
  This is a type-system feature and belongs with §3.

### Risk

Taint tracking is where languages become unpleasant to use. Too strict and every
program is full of `trust(x)` escape hatches, which trains people to sprinkle
them everywhere — at which point the feature is worse than nothing, because it
looks like protection.

---

## 3. A static type checker (v0.3)

### Problem

Everything is checked at runtime. A misspelled field or a wrong `gen<T>`
argument surfaces on the unlucky path, possibly after real money has been spent.

### Design

A single pass over the AST before execution, checking:

- every `gen<T>` names a declared type and supplies every prompt parameter;
- every field access exists on the record's declared type;
- declared return types match what is returned;
- operand types for arithmetic and comparison;
- capability sets are satisfiable along every call path — this is a static
  property and does not need to wait for runtime;
- `budget` sub-allocation, where it is statically knowable.

Almost all of this is straightforward, because the language has no generics, no
subtyping, no inference beyond `let`, and no first-class functions. That
simplicity was chosen partly to make this pass easy.

### Risk

Low. The main cost is that every future language feature must now be typed, and
that constraint bites hardest on the things most likely to be added next — lists
in records, optional fields, union results.

---

## 4. Budget division across `parallel` (v0.3)

*Listed as an open problem in the original blueprint. It still is.*

### Problem

Eight parallel generations race against a shared budget. Whichever charge
arrives after the limit is reached takes the error, so **which** iteration fails
is non-deterministic. Worse, seven may have completed and been paid for while
the eighth fails, leaving a partial result and no clean way to reason about it.

### Candidate designs

| Approach | Behaviour | Objection |
|---|---|---|
| **Equal split** | each iteration gets `remaining / n` | Wastes budget when iterations are uneven; a cheap one holds an allowance it never uses |
| **Reserve-and-return** | each takes an estimated reservation, refunds the remainder | Needs a cost estimate before the call; estimates are poor |
| **First-come, hard stop** *(today)* | shared pot, whoever overruns fails | Non-deterministic, partial results |
| **Fail-fast on projection** | before dispatching, project `n × worst-observed-cost`; refuse the whole block if it cannot fit | Conservative — refuses blocks that would have fit |

I lean toward **fail-fast on projection with an opt-in equal split**, on the
grounds that a block which refuses to start is much easier to reason about than
one that half-completes. But this needs to be tested against real usage rather
than argued from first principles, which is precisely why it is not built yet.

### Risk

Whichever is chosen becomes very hard to change later — it is observable
behaviour that programs will come to depend on.

---

## 5. Recovery beyond `policy` (v0.3)

### Problem

`policy` handles retry and model fallback. It cannot express: *if this fails,
do something else entirely.* There is no `try`, and a failed `gen` terminates
the program.

### Design

Deliberately not exceptions. A result type instead:

```
let r = try gen<Invoice> from extract(document: doc)
if r.failed {
  print("falling back to manual review:", r.error)
  return manual(doc)
}
print(r.value.vendor)
```

`try` converts a failure into a value. Without `try`, failures still terminate —
the default stays loud. This keeps a single failure mechanism rather than adding
a second control-flow path that can be silently swallowed, which is the specific
thing that makes Python's exceptions unpleasant in production agent code.

### Risk

Adds a union-ish type to a language with no unions, which forces some type
system decisions earlier than planned.

---

## 6. Leaving OS threads (v0.4)

### Problem

Measured ceiling is 10,000–20,000 concurrent operations, against a design target
of 100,000. Spawn cost (~230 µs) and virtual address space are the binding
constraints — see `docs/PERFORMANCE.md` §4.

### Design

**Preferred route, added in v0.3: move execution to Go.** The Python front end
emits a portable format (`docs/HBX.md`) and a Go runtime executes it. Goroutines
start at ~2 KB against ~2.2 MB of reserved stack for an OS thread, which is the
difference between ~10,000 and (in principle) ~1,000,000 concurrent calls. The
runtime is written — `go/` — and **has never been compiled**, because there is
no Go toolchain in the environment where it was produced. Verifying it is the
single highest-value next task in this document.

Fallback, if the Go host does not work out:

1. **`asyncio` under the hood.** Keep `parallel for` exactly as it appears to
   the user — no `async`/`await` in the language, ever — and swap the executor
   for an event loop. Removes per-task stack reservation and drops dispatch cost
   by roughly an order of magnitude. Requires provider adapters to be
   async-native, so it pairs with §8.
2. **A real scheduler**, once compiled (§7): green threads with work stealing,
   which is what the blueprint always meant.

The user-facing point is that the concurrency model was designed so this can
change without touching a single Humbaba program. No function colouring means no
migration.

### Risk

Stage 1 is mostly plumbing. Stage 2 is a serious systems project and should not
begin until semantics are frozen.

---

## 7. Compilation (v0.5+)

### Problem

Three of the original goals are unmet by a Python interpreter: single-binary
deployment, fast start (72 ms today against 1–3 ms for Go), and the ability to
run high-volume low-latency pipelines where 41 µs of overhead starts to matter.

### Design

Sequenced so that nothing is thrown away:

1. ~~**Closure compilation in Python.**~~ **Done in v0.2.** The AST compiles to
   Python closures with statically allocated slots. Measured 17.3× over
   tree-walking; the gap to hand-written Python fell from ~141× to 7.1×. It also
   forced the semantics to be written down precisely, which was worth more than
   the speed — several under-specified corners only became visible when
   something had to be decided at compile time rather than looked up at run
   time.
2. **Next: leave CPython.** The remaining 7.1× cannot be recovered from inside
   Python, because every Humbaba operation costs at least one Python operation.
   Options, in increasing order of commitment: emit Python source and let
   CPython execute it directly (reaches parity, never exceeds it); a bytecode VM
   in a compiled extension; a native compiler.
3. **Native host.** ~~50–200× dispatch~~ — **this estimate was wrong and is
   now measured.** A native bytecode VM in C runs Humbaba IR 18.3× faster than the
   Python closure backend and only **2.7× faster than CPython**, because
   CPython's eval loop is itself a tuned C bytecode VM. Order-of-magnitude gains
   over CPython need unboxed, type-specialised code, not a bytecode
   interpreter. What a native host *does* buy, measured: **59× cold start**
   and single-binary deployment. See `docs/HBX.md`.
4. **WASM target** for sandboxed execution of untrusted Humbaba programs — which is
   an interesting thing for a language with a capability system to be able to
   offer.

### Risk

**This is the step most likely to kill the project.** Every one of the languages
in the graveyard — Swift for TensorFlow among them — had excellent
implementation work and no users. Compiling early converts design flexibility
into sunk cost. The gate should be adoption, not enthusiasm: compile when
programs exist that are too slow, not before.

---

## 8. Real providers (v0.2, alongside durability)

### Problem

There is one mock provider. It has the right *shape* — cost, latency, transient
failure, refusal, malformed output — but it does not call anything.

### Design

A thin adapter interface matching what the runtime already assumes:

```python
class Provider:
    def generate(self, model, system, user, schema) -> tuple[dict, float]:
        ...
```

Per-provider concerns that must not leak into the language: schema-constrained
decoding where supported and JSON-mode-plus-validation where not; token counting
for real cost rather than the current character proxy; rate-limit headers mapped
onto hard failure; content-filter refusals mapped onto **soft** failure, which
is exactly the distinction §7.4 exists to draw.

Model names in `fallback` become configuration rather than literals, so a
program does not hard-code a vendor.

### Risk

Provider APIs change monthly. Bake too much in and the language rots; abstract
too far and it cannot express schema-constrained decoding, which is half the
value of `gen<T>`. This is a permanent maintenance tax, not a one-off task.

---

## 9. Language gaps

Small, unglamorous, and all required before anyone writes a real program:

| Gap | Note |
|---|---|
| Assignment | No way to rebind a name. Accumulator patterns are impossible. |
| `and` / `or` / `not` | Conditions can only be a single comparison today. |
| Unary minus | `0 - x` is embarrassing. |
| `while`, `break`, `continue` | Only bounded iteration over lists exists. |
| Nested record types | `type A { b: B }` is rejected. Real extraction schemas need it. |
| Lists in records | Same. |
| Optional fields | Currently a missing field is always a soft failure; sometimes it is just absent. |
| Modules and imports | Everything is one file. |
| String interpolation outside prompts | Only prompt bodies interpolate. |
| User-defined capabilities | The set is closed; a real tool ecosystem needs it open. |

The last one is the most consequential: the capability system's security story
collapses the moment a popular library ships an unrestricted `shell.exec`, and
there is no way to define fine-grained capabilities without this.

---

## Release plan

| Version | Contents | Unlocks |
|---|---|---|
| **v0.1** *(now)* | typed generation, fencing, capabilities, budgets, policies, structured concurrency, replay | the semantics are demonstrable and testable |
| **v0.2** *(partly done)* | ~~closure-compiling backend~~, ~~random fence nonces~~, ~~sequential `for`~~; still to do: `durable`/`step`, real providers, assignment + boolean operators | real programs against real models |
| **v0.3** | type checker, `try`, budget division, nested types | programs that fail at compile time instead of at 3 a.m. |
| **v0.4** | asyncio runtime, modules, user-defined capabilities | production scale and a tool ecosystem |
| **v0.5+** | bytecode VM, then native compilation, then WASM | the single-binary promise |
| **v1.0** | frozen semantics, versioned journal format, stability guarantee | anyone can depend on it |

---

## Problems I do not have answers to

Stated plainly, because a roadmap that only lists solved problems is marketing.

1. **Does anyone actually switch?** Every honest precedent says a better
   language loses to an adequate incumbent with a bigger ecosystem. The wedge —
   deploy alongside Python over HTTP, never rewrite — is the best answer I have,
   and it is not obviously sufficient.

2. **Is `gen<T>` too rigid?** Sometimes you want prose, not a record. There
   needs to be an untyped escape hatch, and the moment it exists people may
   reach for it by default, which would hollow out the central feature.

3. **Do capabilities survive contact with an ecosystem?** They work in a
   single-file program. They work in a curated standard library. Whether they
   survive a package registry is a social question, not a technical one, and
   the honest precedent — every language's security model eventually being
   routed around for convenience — is not encouraging.

4. **Is the durable/`step` restriction tolerable?** Confining side effects to
   `step` blocks is correct and it is a real constraint on how code is written.
   People may simply find it annoying enough to avoid `durable` entirely, at
   which point the headline feature is decoration.

5. **What happens when models stop failing this way?** Much of the design —
   soft failures, schema coercion, fallback models — treats current model
   unreliability as a permanent feature of the landscape. If structured output
   becomes fully reliable, some of this becomes ceremony around a problem that
   no longer exists.
