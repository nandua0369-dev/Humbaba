# Humbaba — Architecture

How v0.1 is put together, for anyone modifying it.

Roughly 1,100 lines of Python, no dependencies outside the standard library.

---

## Pipeline

```
source
  │
  ├─ lexer.py      → [Token]           regex scanner, one pass
  │
  ├─ parser.py     → (types, prompts, fns)
  │                  recursive descent, no backtracking
  │
  ├─ compile.py    → execution  (default: closure compiler)
  ├─ runtime.py    → execution  (--backend tree: reference walker)
  │
  └─ hbx.py        → HBX bytecode ───┬─ hbxvm.py       Python reference VM
                                      └─ go/            Go runtime
        │
        ├─ Ctx      capabilities · budget · policy · function name
        ├─ Env      lexical scope chain
        └─ model.py provider: cost, latency, failure, record/replay
```

Two backends exist, and both are kept deliberately.

`runtime.py` walks the AST directly. It is the reference: the simplest possible
statement of what each construct means, and the thing to read when asking what
Humbaba *does*.

`compile.py` compiles each AST node into a Python closure once, then runs the
closures. 17× faster, and structurally harder to read. It is the thing that
runs.

Keeping both is not sentimentality. A fast backend that quietly disagrees with
the reference is a different language, so `TestBackendEquivalence` asserts they
produce byte-identical output and identical spend on a program exercising types,
prompts, conditionals, both loop forms, and nested calls. Every optimisation is
therefore an A/B measurement against a working implementation rather than a
memory of one.

---

## Modules

### `lexer.py` (~90 lines)

One alternation regex over whitespace, comments, numbers, strings, identifiers
and operators. Keywords are identifiers matched against a set after the fact,
so adding a keyword is a one-line change.

Emits `Token(kind, value, line, col)`. Kinds: `KW`, `IDENT`, `NUM`, `STR`, `OP`,
`EOF`.

### `ast.py` (~120 lines)

Plain dataclasses, no methods, no visitor. Declarations, statements and
expressions in three groups. Most carry a `line` for error messages.

### `parser.py` (~280 lines)

Recursive descent. A program is a flat sequence of declarations collected into
three dicts keyed by name — there is no scope for declarations, and a duplicate
name silently overwrites (a real gap; the type checker in ROADMAP §3 should
reject it).

Precedence is encoded as a ladder of methods: `comparison` → `additive` →
`multiplicative` → `postfix` → `primary`. `gen`, `for` and `parallel for` are
handled in `primary`, which is why they are expressions rather than statements
and can appear anywhere a value can.

One ambiguity worth knowing about: `<` is both a comparison operator and the
bracket in `gen<T>`. It is unambiguous only because `gen` is a keyword and the
parser commits to the generic form immediately after seeing it. Any future
generic syntax elsewhere will need real lookahead.

### `model.py` (~150 lines)

The provider. Deliberately stateful and deliberately fake, but with the same
failure surface as a real one:

| Concern | Implementation |
|---|---|
| Cost | character count × per-model rate |
| Latency | `time.sleep` per model |
| Hard failure | `TransientError`, fired with probability `--chaos` |
| Soft failure | `RefusalError`, or malformed output via `--overloaded` |
| Record/replay | SHA-256 of (model, system, user, schema) → response |
| Injection | scans the *instruction surface* and obeys what it finds there |

`strip_fenced()` is the heart of the injection demo: it removes fenced regions,
leaving what the model would legitimately treat as instructions. If a payload
survives that removal, the mock obeys it and attempts a tool call — which is
precisely the behaviour a real model exhibits and the reason capabilities exist.

`fabricate()` reads only the fenced payload, never the scaffolding. An early bug
had it reading the whole message, so hex digits from the fence nonce leaked into
extracted numbers — a good illustration of why the data/instruction boundary has
to be enforced on both sides.

### `runtime.py` (~420 lines)

The interpreter.

**`Ctx`** is the dynamic context threaded through every evaluation: the
capability set, the budget frame, the enclosing policy, and the current function
name for error messages. It is explicit rather than thread-local precisely so
`parallel for` can hand the same context to worker threads without ceremony.

**`Env`** is a parent-linked scope chain. Blocks create a child scope; `let`
binds in the innermost. There is no assignment, so there is no write-through.

**`Budget`** is a parent-linked chain with the same shape. `charge()` walks to
the root, checks every limit, then applies the charge to every frame — two
passes under one global lock, so a partial charge can never be observed.

**Capability checks** happen in two places: `require()` before a privileged
operation, and a subset check in `call_fn` when entering a function. The second
is the one that matters — it is what makes capabilities attenuate rather than
merely exist.

**`eval_gen`** is the largest function, and the ordering inside it is load-bearing:
budget is charged *before* coercion, so a malformed response still costs money.

**`eval_parallel`** uses a `ThreadPoolExecutor` sized to `limit`, submits every
item, and collects by index so results are ordered by submission. Any exception
cancels the outstanding futures before propagating.

### `compile.py` (~450 lines)

The fast backend. `Compiler` walks the AST once and emits closures of the shape
`f(slots, ctx) -> value`.

**Slots.** Every binding gets an integer index allocated at compile time, and a
function's frame is a flat Python list. `Scope` resolves names to indices during
compilation; at run time a variable read is `s[3]`. Shadowing works because
shadowed names get distinct slots.

**Returns** use a `Ret` marker object rather than a raised exception. Raising
costs roughly ten times as much, on a path that fires on every function call.

**`parallel for`** copies the frame per task (`s[:]`). This is safe precisely
because Humbaba has no assignment: nothing an iteration does can be observed
outside it. Adding assignment to the language means revisiting this.

**Prompts** are segmented at compile time into alternating literal strings and
parameter indices, so rendering is a `"".join` rather than a `str.replace` per
parameter per call.

**Cassette keys** are built from the *inputs* — model, prompt name, schema tag,
raw argument values — not the rendered message. That is faster, and it means
the fence nonce no longer needs to be deterministic, which is why it is now
random.

`Machine` holds the parts that are not the program: the provider, the output
lock, and the capability implementations.

### `hbx.py` + `hbxvm.py` (~700 lines)

The portable back end. `hbx.py` compiles the AST to a stack-machine bytecode
that carries the enforcement primitives — `REQUIRE`, `GEN`, taint marks,
budget declarations — so a host cannot execute a program while ignoring them.
`hbxvm.py` executes it and exists to state what each instruction means, not to
be fast.

Full format and instruction set in `docs/HBX.md`.

### `go/` (~805 lines, **uncompiled**)

The intended production host: IR loader, VM, and the agent runtime that only
makes sense in Go — budgets, capability attenuation, fencing, and a generic
`ParallelFor` bounded by a semaphore channel with context cancellation.

It has never been through `go build`. See `go/README.md`, which says so first.

### `cli.py` (~60 lines)

Argument parsing, a run, and a one-line summary: gen calls, spend against limit,
cassette hits versus live calls, blocked tool calls, elapsed time.

---

## Threading model

Only `parallel for` creates threads. Shared mutable state across them is exactly
two things:

1. **The budget chain** — guarded by a single module-level lock.
2. **stdout** — guarded by the interpreter's output lock.

Everything else is per-iteration scope. There is no assignment in the language
and no way to mutate an outer binding, which is what makes this safe without
further machinery. That property is worth preserving deliberately: adding
assignment (ROADMAP §9) means revisiting it.

---

## Extension points

**Adding a builtin function**: add to `BUILTIN_FNS` in `runtime.py`.

**Adding a capability**: add a branch in `call_capability` and a name to
`NAMESPACES` if it needs a new prefix. The capability is then automatically
enforceable, because enforcement is centralised in `require()`.

**Adding a statement**: a dataclass in `ast.py`, a branch in `Parser.statement`,
a branch in `exec_stmt`.

**Adding an expression**: a dataclass, a branch in `Parser.primary` (or the
precedence ladder if it is an operator), a branch in `eval`.

**Swapping the provider**: implement `generate(model, system, user, schema,
tool_invoker, notify) -> (dict, cost)` and pass it to `Interpreter`. Nothing
else in the runtime knows what a provider is.

---

## Testing

`tests/test_humbaba.py` — 14 tests, structured around guarantees rather than
functions:

| Group | Asserts |
|---|---|
| `TestCapabilities` | undeclared capability refused; callee cannot amplify; model tool call bounded by signature |
| `TestInjection` | fencing removes text from the instruction surface; untrusted params are fenced; fenced injection triggers no tool call |
| `TestBudget` | overspend halts; child cannot exceed parent's remaining; child spending charges parent |
| `TestGeneration` | output coerced to declared type; soft failure falls back; unrecoverable failure surfaces |
| `TestReplay` | replay is identical, free, and hits the cassette |
| `TestConcurrency` | parallel preserves order |
| `TestLoops` | sequential `for` collects each iteration's value |
| `TestBackendEquivalence` | both backends give identical output, spend, and errors |
| `TestIR` | the IR reference VM matches the closure backend on four programs |

The tests assert the *properties the README claims*, which means a broken claim
fails the suite rather than quietly becoming untrue.

`bench/bench.py` measures the front end, dispatch, per-`gen` overhead, task
dispatch cost, concurrency scaling, and thread capacity. It reports when a
measurement is meaningless on the host — the GIL benchmark on a single-core
machine says so rather than reporting a number.
