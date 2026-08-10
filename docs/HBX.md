# HBX — the Humbaba executable format

*Version 2. Supersedes the register-based IR of v1, now removed.*

---

## Why this exists

The previous IR covered the *compute subset*: arithmetic, branching, calls,
lists, print. Everything that makes Humbaba what it is — `gen`, capabilities,
budgets, taint, `parallel`, `durable` — stayed in the Python runtime.

The consequence was that no real Humbaba program could be compiled. Of the
thirteen shipped examples, exactly one was executable by the C VM and the Go
runtime, and it had to be written specially. "Ship a single binary with a 4 ms
cold start" was not true: the binary could run arithmetic.

HBX exists so that enforcement lives in the artefact rather than in the front
end. A host that executes HBX enforces capability attenuation, taint, and
budgets **because the format carries them**, not because it happens to be
written in Python.

## Design decisions, and why

**Stack machine, not registers.** The previous IR allocated virtual registers.
A stack discipline is simpler to generate correctly, simpler to verify, and
makes the enforcement instructions natural: `GEN` consumes its arguments and
leaves a tainted value. Peak stack depth is computed at compile time so a host
can allocate once.

**Enforcement is instructions, not metadata.** `REQUIRE`, `CHARGE`, `FENCE`
and `TAINT` are opcodes. A host cannot execute the program while ignoring
them, because they are in the instruction stream. Metadata can be skipped;
instructions cannot.

**Capabilities are interned and intersected at call time.** Each function
header declares its capability set as indices into a table. `CALL` computes
the callee's set as the intersection of the declared set and the caller's,
exactly as the interpreter does. The attenuation rule is therefore a property
of the format.

**Taint is a value property, not a static annotation.** Every runtime value
carries a taint bit. `GEN` marks its result tainted if any input was tainted.
`REQUIRE` refuses a tainted operand unless the instruction carries the
`allow_tainted` flag, which the compiler only emits for an explicit `unwrap`.

**Text format.** Binary would be smaller and marginally faster to load. Text
diffs, greps, and survives being pasted into a bug report. Load time is
~0.1 ms against an 800 ms model call; compactness is not worth the opacity.

## File structure

```
HBX 2
K <n>                     constant pool, n entries
  N <float>                 number
  S <string>                string, backslash-escaped
  B <0|1>                   boolean
  Z                         nil
Y <n>                     capability names, n entries
  <name>
T <n>                     record types, n entries
  <name> <field>:<type>[?][,...]
P <n>                     prompts, n entries
  <name> <sysconst> <userconst> <param>[,...]
F <n>                     functions, n entries
  <name> <arity> <nlocals> <maxstack> <caps> <budget> <taint> <durable>
    <instruction>...
  ENDF
```

`caps` is a comma-separated list of capability indices, or `-`.
`budget` is a float, or `-` for uncapped.
`taint` is a comma-separated list of parameter positions declared `untrusted`,
or `-`.
`durable` is `1` for a `durable fn`, else `0`.

## Instruction set

Operands are decimal integers. `→` describes the stack effect.

### Values and locals

| Op | Operands | Effect |
|---|---|---|
| `PUSHK` | k | → const[k] |
| `LOAD` | n | → local[n] |
| `STORE` | n | v → |
| `POP` | | v → |
| `DUP` | | v → v v |

### Arithmetic and comparison

`ADD` `SUB` `MUL` `DIV` `MOD` — `a b → a∘b`
`NEG` `NOT` — `a → ∘a`
`LT` `GT` `LE` `GE` `EQ` `NE` — `a b → bool`

Taint propagates through every one: if either operand is tainted, so is the
result.

### Control flow

| Op | Operands | Effect |
|---|---|---|
| `JMP` | target | — |
| `JZ` | target | c → , jumps if falsey |
| `JNZ` | target | c → , jumps if truthy |

`AND` and `OR` are compiled to jumps, so short-circuit semantics are in the
instruction stream rather than in a host's interpretation of an opcode.

### Aggregates

| Op | Operands | Effect |
|---|---|---|
| `LIST` | n | v₁..vₙ → [v₁..vₙ] |
| `INDEX` | | list i → list[i] |
| `LEN` | | list → n |
| `APPEND` | | list v → list |
| `RECORD` | t, n | v₁..vₙ → record of type t |
| `FIELD` | k | record → record.field(k) |

### Calls

| Op | Operands | Effect |
|---|---|---|
| `CALL` | f, argc | a₁..aₙ → result |
| `RET` | | v → (returns v) |
| `RETNIL` | | → (returns nil) |

`CALL` intersects the callee's declared capabilities with the caller's current
set. This is the attenuation rule, and a host that skips it is not executing
HBX.

### Enforcement

| Op | Operands | Effect |
|---|---|---|
| `REQUIRE` | y, allow_tainted | — ; refuses unless capability y is held |
| `CHARGE` | | amount → ; charges the budget chain |
| `RESERVE` | | amount → ; reserves against the chain, refunded by `RELEASE` |
| `RELEASE` | | amount → |
| `FENCE` | | v → fenced-string |
| `TAINT` | | v → tainted v |
| `UNTAINT` | k | v → untainted v ; k is the required written reason |
| `GEN` | t, p, argc, model, retry, fallback | a₁..aₙ → value of type t |

`GEN` is the central instruction. It requires the `model` capability, fences
any tainted argument, charges the budget, coerces the response to type `t`,
and marks the result tainted if any input was. All of that is specified here
rather than left to the host, so three hosts cannot disagree about it.

**Retry and fallback are operands, not runtime state.** `policy` is lexically
scoped — it governs the `gen` calls written inside it, not the functions those
calls invoke — so the compiler resolves it and writes `retry` and `fallback`
onto each `GEN`. No host needs a policy stack. `fallback` is a constant index,
or `-1` for none.

The two failure modes are treated differently, and a host must distinguish
them:

- **Transient** — the provider did not answer. Only retrying can help; the
  attempt is spent.
- **Refusal** — an answer arrived but did not match the declared type. Another
  model may do better, so the fallback is tried *first* and a retry is spent
  only when there is no fallback, or it has already been used.

One consequence, inherited from the reference interpreter and preserved for
equivalence: `fallback` with `retry: 0` never fires, because the switch to the
fallback model needs a further loop iteration to run in. A policy that wants a
fallback needs at least `retry: 1`.

### Concurrency and durability

| Op | Operands | Effect |
|---|---|---|
| `PARALLEL` | body, limit | list → list ; body is a function index |
| `STEP` | k, body | → v ; k names the step, body is a function index |
| `TRY` | body | → record{ok,value,error} |

`PARALLEL` runs `body` over each element with at most `limit` in flight and
returns results in submission order. `STEP` journals its result under the name
in const[k]; on replay the body is not executed.

The bodies of `PARALLEL`, `STEP` and `TRY` are lifted into synthetic functions
at compile time. This keeps the instruction stream flat — no nested blocks in
the format — so a host needs no structured control stack.

## Opcodes specified but not yet emitted

`FENCE`, `TAINT`, `UNTAINT`, `CHARGE`, `RESERVE`, `RELEASE` and `APPEND` are
defined above but the current compiler never emits them: fencing and charging
happen inside `GEN`, and taint is applied from the function header's untrusted
parameter list rather than by an instruction.

They are specified because the operations need to be expressible — an explicit
`unwrap` needs `UNTAINT`, and a host-side tool call that costs money needs
`CHARGE`. A host should implement them. `tests/test_humbaba.py` checks only
that every opcode the compiler *does* emit is handled by every host, so an
unimplemented one of these will not be caught until something emits it.

## What a conforming host must do

1. Enforce `REQUIRE` against the current frame's capability set
2. Intersect capabilities on `CALL`
3. Propagate taint through every value-producing instruction
4. Refuse tainted operands at `REQUIRE` unless `allow_tainted` is set
5. Charge and check budgets across the whole ancestor chain
6. Fence tainted arguments to `GEN` before they reach a model

A host that does 1–6 is conforming. A host that executes the arithmetic and
ignores the rest is not running Humbaba; it is running a calculator.

## Verification

`tests/test_hbx.py` compiles every shipped example to HBX, runs it on the
reference VM, and asserts the output matches the tree-walking interpreter
exactly — including spend, blocked capabilities, and taint refusals. That test
is the definition of conformance; a new host passes it or it is not a host.
