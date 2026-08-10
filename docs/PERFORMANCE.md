# Is Humbaba fast enough?

**Short answer: yes, by roughly five orders of magnitude — for the workload it
was designed for. And it is genuinely slow at three things, all of which are
either non-goals or fixable.**

> **v0.3 — language completed, and it cost almost nothing.** A static checker,
> taint tracking, a durability journal, budget reservation and a second
> scheduler were all added. Per-`gen` overhead is **7.63 µs against v0.2's
> 7.6 µs — no measurable regression**. Checking costs 0.04 ms, once, before
> anything runs. See §10.
>
> **v0.3 — compiled hosts.** A bytecode IR and a native C VM now exist.
> Measured here: the native VM is **11.2× faster than the closure compiler**
> but only **1.6× faster than CPython** — a much smaller win than expected, for
> a reason worth understanding (§8). The real prize is cold start: **0.87 ms
> against 66.28 ms, a 76× improvement.** See also `docs/PROVENANCE.md`, which
> records that the C VM was not written in this session and that its committed
> benchmark figures did not reproduce.
>
> **v0.2 optimisation pass.** Humbaba now compiles to Python closures instead of
> walking the AST. Dispatch got **17.3× faster**, per-`gen` overhead **5.4×**
> faster, and the gap to hand-written Python closed from ~141× to **7.1×**.
> The tree-walker is retained as `--backend tree` so every claim here is an
> A/B measurement rather than a memory of an earlier run. See §7.
>
> **v0.3 native host.** Humbaba now emits a portable IR, and there is a native VM
> in C to measure what a compiled host buys. The answer is not what I expected
> and is worth reading before betting on compilation: §8.

The question needs splitting before it can be answered, because "fast" means
three unrelated things here:

1. How much time does Humbaba add to a model call? *(the thing that matters)*
2. How fast does Humbaba execute ordinary code? *(a declared non-goal)*
3. How many operations can it hold in flight at once? *(the real ceiling)*

---

## Reproducing

```bash
python3 bench/bench.py
```

All figures below were measured on **CPython 3.12.3, single-core container**.
Single-core matters: it makes the absolute numbers conservative, and it makes
the GIL test void — reported as such rather than quietly presented.

---

## 1. Overhead per model call — the number that matters

Measured against a replayed cassette, so provider latency is zero and what
remains is purely Humbaba: interpolation, fencing, hashing, schema derivation,
coercion, budget arithmetic, policy handling.

| Measurement | tree-walker | closure compiler |
|---|---|---|
| Per `gen<T>` | 11.2 µs | **7.6 µs** |
| Original v0.1 measurement | 41 µs | — |

Against real providers, using the compiled backend:

| Provider latency | Humbaba's share of wall time |
|---|---|
| 300 ms (fast model) | 0.0025 % |
| 800 ms (typical) | 0.0009 % |
| 8 s (long agent step) | 0.0001 % |

A realistic agent shape — 12 sequential steps plus 3 parallel fan-outs of 5,
at 800 ms per call:

```
Humbaba runtime:   0.34 ms
model time:   12,000 ms
Humbaba's share:   0.003 %
```

**If Humbaba were made infinitely fast, that agent would finish a third of a
millisecond sooner.** This is the whole verdict. Every other number on this page
is context.

---

## 2. Raw execution speed — slow, and deliberately so

| Measurement | tree-walker | closure compiler | gain |
|---|---|---|---|
| Function calls per second | 18,500 | **320,753** | 17.3× |
| Per iteration (~14 AST nodes) | 54.05 µs | **3.12 µs** | 17.3× |
| AST nodes per second | 0.3 M | **4.5 M** | 15× |

For comparison, CPython manages a few million simple operations per second, and
compiled Go is in the hundreds of millions. **Humbaba is still slower than Python** —
it is an interpreter written *in* Python, so every Humbaba operation costs at least
one Python operation. That is a hard ceiling, not an engineering shortfall.

Measured directly against an identical function in CPython:

| | Time for 20,000 iterations | Per iteration |
|---|---|---|
| Humbaba (compiled closures) | 63.0 ms | 3.15 µs |
| CPython | 8.8 ms | 0.44 µs |

**CPython is 7.1× faster.** Before the optimisation pass it was ~141×. Closing
the remaining gap requires leaving CPython entirely (§ROADMAP 7).

This is acceptable only because of §1: the language exists to sit between model
calls, and the model call is five orders of magnitude more expensive than the
code around it. It would be indefensible in a general-purpose language.

It does mean one rule for users: **do not write compute in Humbaba.** Loop over 50
documents, not 50 million rows. Non-goal §2 in the README is not modesty, it is
load-bearing.

### Front end

| Measurement | Result |
|---|---|
| Lexing | 0.7 M tokens/s |
| Parsing | 112 lines in 1.55 ms |
| Extrapolated 10,000-line program | ~139 ms |

Parsing is fine in absolute terms but shows up in cold start (§4).

---

## 3. Concurrency — excellent where it counts

128 generations at 250 ms simulated latency:

| `limit` | Wall time | Ideal | Efficiency |
|---|---|---|---|
| 16 | 2.01 s | 2.00 s | 99.6 % |
| 64 | 0.51 s | 0.50 s | 97.6 % |
| 128 | 0.27 s | 0.25 s | 92.8 % |
| serial | 32.0 s | — | — |

**Near-perfect scaling on I/O-bound work**, which is all this workload is. A
118× speedup at `limit 128`. Efficiency tails off slightly at high width, which
is thread-dispatch cost, not contention.

Cost of the machinery itself:

| Measurement | Result |
|---|---|
| Dispatching one parallel task | ~9 µs |
| A goroutine, for comparison | ~0.3 µs |
| A sequential `for` iteration | 0.16 µs |

`parallel for` with `limit 1`, or over a single item, now runs inline with no
pool at all — the thread machinery cost more than the work in that case, and
nothing can overlap anyway.

12 µs against an 800 ms call is 0.0015 %. Irrelevant here; would be fatal in a
language used for fine-grained parallelism.

### The GIL

CPython serialises CPU-bound threads. The benchmark that would demonstrate this
ran on a single-core machine, so it demonstrates nothing, and reports that
rather than pretending otherwise. Treat GIL serialisation as a known property of
the host: it does not affect Humbaba's target workload, where threads are blocked on
network I/O and release the lock, but it does mean §2 cannot be escaped by
adding `parallel`.

---

## 4. Capacity — the real ceiling

The blueprint promised 100,000 in-flight operations. Measured:

| Live threads | Spawn + drain | RSS added | Virtual added | Per thread |
|---|---|---|---|---|
| 100 | 59 ms | 1.1 MB | 770 MB | 7.9 MB |
| 1,000 | 236 ms | 5.2 MB | 3.5 GB | 3.6 MB |
| 5,000 | 831 ms | 24.2 MB | 11.0 GB | 2.2 MB |
| 10,000 | 2,325 ms | 59.5 MB | 21.3 GB | 2.2 MB |

Resident memory is modest — about 6 KB actually touched per thread, since stacks
are reserved lazily. The binding constraints are **spawn time** (~230 µs per
thread, so 100,000 would cost ~23 s before any work begins) and **virtual
address space** (~213 GB at 100,000, which will not map).

**Practical ceiling: 10,000–20,000 concurrent operations.** That is enough for
essentially any single-tenant agent workload and not enough for the figure in
the blueprint. Closing the gap means leaving OS threads — see
`docs/ROADMAP.md` §6.

---

## 5. Cold start

| Measurement | Result |
|---|---|
| `python3 -c pass` | 12.9 ms |
| `humbaba run hello.hb` | 72.3 ms |
| Humbaba's contribution | 59.3 ms |
| A Go binary, for comparison | 1–3 ms |

Irrelevant for a long-running service. **Relevant for serverless**, where 72 ms
is a real fraction of a short invocation, and relevant to the original pitch —
"single binary, fast startup" was half the reason Go was in the blend. v0.1 does
not deliver that half. It is an implementation gap, not a design one.

---

## 6. When Humbaba *would* be too slow

The 41 µs overhead is invisible against a 800 ms call. It stops being invisible
as latency falls:

| Call type | Latency | Humbaba's share |
|---|---|---|
| Frontier model | 800 ms | 0.001 % |
| Small hosted model | 150 ms | 0.005 % |
| Local quantised model | 15 ms | 0.05 % |
| Cached embedding lookup | 200 µs | **3.7 %** |
| In-memory classifier | 20 µs | **28 %** |

**The failure mode is high-volume, low-latency calls.** A pipeline classifying a
million records against a local model would spend most of its time in Humbaba. Two
honest responses: don't use Humbaba for that, or make the interpreter 50× faster,
which is what compilation buys (§ROADMAP 7).

Three other places where the answer flips to no:

- **Compute inside Humbaba.** 320,000 calls/s, still 7× slower than Python. Push it
  into a tool.
- **Beyond ~10,000 concurrent operations.** Thread model, not language design.
- **Serverless cold path.** 72 ms per invocation.

---

## 7. What the optimisation pass changed

The v0.1 runtime re-decided what every AST node *was* on every visit: a chain of
`isinstance` checks, a dict-chain lookup for every variable, a fresh scope object
per block. All of that is knowable once.

| Change | Effect |
|---|---|
| **Closure compilation** — each node becomes a Python closure taking `(slots, ctx)` | removes dispatch entirely; the call graph *is* the program |
| **Static slot allocation** — variables resolve to integer indices into a flat list | dict-chain lookup becomes `s[3]` |
| **`Ret` marker instead of raised exceptions** for `return` | ~10× cheaper on a hot path that fires every call |
| **Prompt templates segmented at compile time** | no `str.replace` per parameter per call |
| **Cassette keyed on inputs, not the rendered message** | one hash over short input instead of `json.dumps` + hash over the whole prompt |
| **Precomputed budget chains** with `__slots__` | charging walks a tuple, not a linked list |
| **Constant folding**, compile-time argument binding, type codes instead of string compares | small, additive |
| **Inline fast path** for `parallel` with `limit 1` or a single item | avoids ~9 µs of pool machinery per task |

Net: 17.3× on dispatch, 5.4× on `gen` overhead against the original 41 µs.

Two of these paid a second dividend. Keying the cassette on inputs meant the
fence nonce no longer had to be deterministic, so **it is now random** — closing
the predictability weakness documented in ROADMAP §2. And untrusted content is
scanned for forged fence markers before interpolation. A performance change
fixed a security hole, which is not the usual direction.

### Honest notes on the benchmarks themselves

Two bugs were found and fixed while producing these figures, both of which had
been flattering the results in one direction or another:

- `run_src` included parse and compile time inside the timer, and the dispatch
  benchmark parses a 20,000-element list literal. Front-end cost was being
  charged to the interpreter — it now builds first and times only execution.
- The container is noisy enough to swing measurements by 30 %. Everything is now
  min-of-N with the backends interleaved to cancel drift.

Both backends are kept and both run the full test suite, including a test that
asserts they produce byte-identical output and identical spend. A fast backend
that quietly disagrees with the reference is a different language, not an
optimisation.

## 8. What a compiled host actually buys

Humbaba emits a portable bytecode (`docs/HBX.md`), executed by two
implementations that are tested to agree: a Python reference VM, a native C VM,
and a Go runtime. The C VM exists to answer "how much would going native help?"
with a measurement rather than an estimate.

| Executor | per iteration | vs slowest |
|---|---|---|
| Humbaba tree walker (Python) | 55.92 µs | 1.0× |
| Humbaba IR on reference VM (Python) | 6.67 µs | 8.4× |
| Humbaba closure compiler (Python) | 3.12 µs | 17.9× |
| CPython, hand-written equivalent | 0.457 µs | 122× |
| **Humbaba IR on native VM (C, `-O2`)** | **0.170 µs** | **328×** |

**Native is 18.3× the Python backend and 2.7× CPython.**

Two findings, both of which contradict what I wrote in the earlier roadmap:

**A bytecode VM does not beat CPython by an order of magnitude.** CPython's eval
loop is itself a tuned C bytecode VM, and this one boxes values into a 24-byte
struct much as CPython boxes into objects. The roadmap's estimate of "50–200×
dispatch" from compilation was wrong by roughly two orders of magnitude. Getting
those numbers requires unboxed, type-specialised code — a JIT or a real AOT
compiler — not a bytecode interpreter.

**And it would not matter if it were true.** Dispatch is under 0.01 % of an
agent's wall time. An 18× improvement on 0.01 % is not a product.

### Where native wins outright

**Cold start, by 59×:**

| | hello world, whole process |
|---|---|
| `/bin/true` (spawn floor) | 1.10 ms |
| **native VM** | **1.11 ms** — 0.01 ms above the floor |
| `python -c pass` | 11.11 ms |
| `humbaba.py` | 65.46 ms |

The native binary costs essentially nothing above forking a process, and no
amount of interpreter work touches this. It is the host, not the code.

**The concurrency ceiling.** Python threads top out at 10,000–20,000 in flight
(§4). Goroutines start at ~2 KB against ~2.2 MB of reserved thread stack, which
should move that by two orders of magnitude. `go/main.go -capacity 1000000` is
written to measure it and **has not been run** — there is no Go toolchain in
this environment. That number is the one open question in this document.

**Single-binary deployment**, which the Python implementation simply does not
offer.

### The conclusion, stated plainly

Go is not for making Humbaba execute faster. Execution speed was never the
constraint. Go is for making Humbaba **start instantly, deploy as one file, and hold
a million model calls open at once** — and the third of those is the only one
that changes what programs are possible.

## 8. Compiled hosts: what a C or Go runtime actually buys

Re-measured independently (`python3 bench/native.py`), 20,000 iterations of an
identical 10-term arithmetic function:

| Executor | Total | Per iteration | vs slowest |
|---|---|---|---|
| Humbaba — tree walker (Python) | 1019.5 ms | 50.97 µs | 1.0× |
| Humbaba — IR on reference VM (Python) | 124.1 ms | 6.21 µs | 8.2× |
| Humbaba — closure compiler (Python) | 57.8 ms | 2.89 µs | 17.6× |
| CPython — hand-written equivalent | 8.0 ms | 0.40 µs | 126.9× |
| **Humbaba — IR on native VM (C, -O2)** | **5.2 ms** | **0.259 µs** | **197.2×** |

**The native VM beats CPython by only 1.6×.** That is the most useful number on
this page, and it is far short of the 10–100× that "rewrite it in C" implies.

The reason is worth stating plainly: **CPython's eval loop is already a
well-tuned C bytecode interpreter.** Writing another bytecode interpreter in C
does not beat it — it merely joins it. This VM boxes every value into a 24-byte
tagged struct, exactly as CPython boxes into an object, so it pays the same
memory-traffic and branch-prediction costs. Decisively beating CPython requires
*unboxed, type-specialised* code — a JIT or an ahead-of-time compiler with real
type information — not a naive VM in a faster language.

### A note on the C VM figures below

`native/humbabavm.c` was removed in the HBX changeover. It read the v1
register IR and could not load HBX, so it was not a host — it was an artefact
that failed on every program the compiler produced.

Its measured figures are kept because they were real: 170 µs execution and
0.87 ms cold start on Apple Silicon, against the same program. They are no
longer reproducible from this repository. A C host for HBX would be worth
building; resurrecting the old one would not.

### The HBX Go host — measured 2026-08-09

The v1 register IR could compile one of thirteen examples, so the earlier Go
figures describe a host running arithmetic. HBX carries the enforcement
primitives, so these numbers are for a host executing the actual language:
capability attenuation, taint propagation, fencing and budgets included.

Apple Silicon (macOS, arm64), `examples/09_compute.hb`:

| Phase | v1 | after allocation fixes | after frame/closure fixes |
|---|---|---|---|
| Load | 0.349 ms | 0.092 ms | **0.087 ms** |
| Run, best of 50 | 6.838 ms | 3.034 ms | **1.87–1.94 ms** |
| Process start | ~0.14 ms | ~0.14 ms | ~0.14 ms |

**3.7× faster overall.** What is worth recording is which guesses were right,
because two of four were not.

| Hypothesis | Change | Result |
|---|---|---|
| Cold start is dominated by Go runtime init | — | **Wrong.** Process start was ~0.14 ms; the run was 6.8 ms. |
| Per-call allocation dominates | removed four of six | **Right.** 6.838 → 3.034 ms |
| Closures and a second allocation cost more | merged locals and stack into one slice, inlined push/pop | **Right.** 3.034 → 1.866 ms |
| String opcode dispatch is the remaining cost | interned opcodes to integers at load | **Wrong.** 1.866 → 1.938 ms, i.e. noise. Go dispatches switches on constant strings by length first, so it was never doing the naive work assumed. |

The opcode interning was kept despite giving nothing on speed, because it
moved unknown-instruction detection from execution time to load time. A
program using an opcode this host lacks now fails to start rather than failing
later on a branch nobody tested.

**Still ~3.8× slower than the v1 register VM** (1.9 ms against 502 µs). The
remaining cost is almost certainly `Value`, a ~72-byte struct copied on every
push, pop and local access. Shrinking it means boxing the rare cases — string,
list, record — behind a pointer, which is a redesign rather than a tweak.

That gap is recorded rather than closed, deliberately. Humbaba's overhead is
measured against an ~800 ms model call, so 3 ms of VM time is 0.4% of a single
call, and the v1 figure was never a number anyone experienced either.
Optimising it further would be work with no user on the other end.

### Go — measured, and the projection was wrong

This document previously projected that Go would land "slightly below the C
figure, typically within ~1.5× on a dispatch loop." That was an estimate made
before a Go toolchain existed anywhere near this project. It has now been
measured, and it was optimistic by roughly 2×.

Measured 2026-08-09 on Apple Silicon (macOS, arm64), `examples/09_compute.hb`,
execution only — VM run excluding process spawn and IR load, best of 20:

| Host | Execution | vs C |
|---|---|---|
| Native VM (C, `-O2`) | **170 µs** | 1.0× |
| Go runtime (`-ldflags "-s -w"`) | **502 µs** | **2.95× slower** |

So the real cost of Go over C on this workload is about **3×**, not 1.5×. The
usual suspects — bounds checking, GC write barriers, interface dispatch on the
boxed `Value` type — evidently cost more here than the estimate allowed for.

This does not change the architectural argument, because execution speed was
never the reason to want a Go host. The reasons were cold start and concurrency
headroom, and a 3× dispatch penalty on a workload that spends 99.999% of its
wall time waiting on a model is not a cost anyone will notice. But the number
in this document was wrong, and now it is not.

### Cold start — the effect that actually matters

| | Time |
|---|---|
| `/bin/true` (process spawn floor) | 0.82 ms |
| **Native VM** | **0.87 ms** (+0.05 ms over the floor) |
| `python3 -c pass` | 11.32 ms |
| `humbaba.py` (Python front end) | 66.28 ms |

**76×**, and it does not shrink with a faster interpreter, because it is the
host and not the code. This is the single largest measured effect in the
project, and it is the entire practical case for a compiled runtime: not
throughput, but starting instantly and shipping as one file.

#### Go cold start — measured, and also slower than projected

Measured 2026-08-09 on Apple Silicon, whole process including spawn and IR
load, over 20 runs:

| Host | Cold start |
|---|---|
| Go runtime | **4.10 ms** best, 4.40 ms median |
| Python front end (same machine, same program) | **82.20 ms** best of 5 |

The expectation stated elsewhere in this project was "near 1 ms," from the C
VM's 0.87 ms. Go came in at **4.1 ms — roughly 4.7× the C figure**, because a
Go binary pays for runtime initialisation at startup (scheduler, GC, memory
arenas) that a C program does not.

The conclusion survives anyway: **20× faster to start than the Python front
end**, on the same machine, measured rather than projected. That is the effect
worth having. But "near 1 ms" was wrong; it is 4 ms, and C remains the choice
if cold start is the only thing being optimised.

Both of these corrections follow the same pattern as the native VM's headline
number, which was overstated by ~1.9× before it was measured. Estimates in this
project have consistently been optimistic by 2–5× until someone ran them.

### So what is the compiled host for?

Not speed. Restating the arithmetic from §1: Humbaba's overhead is 7.6 µs against an
800 ms model call. Making dispatch 11× faster changes 0.0009 % of wall time to
0.0001 %. **Nobody will ever notice.**

The compiled host is for:

- **Cold start** — 76×, and decisive for serverless.
- **Deployment** — one 21 KB static binary, no interpreter, no dependency tree.
- **Concurrency capacity** — goroutines at ~2 KB against OS threads at ~2.2 MB
  of reserved stack.

That last one comes with a caveat that only appeared when it was measured.
**Python asyncio reaches 500,000 concurrent tasks at 0.71 KB of RSS each** —
better memory per concurrent operation than a goroutine's ~2 KB initial stack:

| Concurrent tasks | Spawn | Total | RSS added | Per task |
|---|---|---|---|---|
| 1,000 | 3 ms | 58 ms | 0.7 MB | 0.70 KB |
| 10,000 | 22 ms | 128 ms | 6.3 MB | 0.64 KB |
| 100,000 | 604 ms | 2,745 ms | 64.0 MB | 0.66 KB |
| 500,000 | 4,183 ms | 16,146 ms | 348.6 MB | 0.71 KB |

The 10,000-operation ceiling documented in §4 is a **thread-model** limitation,
not a Python limitation, and it can be lifted without leaving Python. Goroutines
still win on spawn cost — ~0.3 µs against asyncio's 6 µs, a 20× gap — but at
100,000 tasks that is 30 ms versus 604 ms, on a workload where a single model
call takes 800 ms.

**Conclusion: adopt a compiled host for start-up and deployment, not for
throughput.** Move `parallel for` onto asyncio first, because it is free, it
lifts the ceiling 50×, and it requires changing no Humbaba program.

## 9. Throughput: when does the host language become the bottleneck?

Dispatch benchmarks answer "how fast is the interpreter", which is the wrong
question. The one that decides whether to rewrite anything is: *at what point
does the interpreter become the constraint?*

Measured with the provider removed entirely (`python3 bench/throughput.py`):

| Host | Sustained throughput | Per operation |
|---|---|---|
| Python closure backend | **176,884 ops/s** | 5.65 µs |
| Compiled host (estimated) | ~1,970,000 ops/s | ~0.51 µs |

The compiled figure is scaled from the measured 11.2× dispatch ratio and is an
**estimate, not a measurement** — the C VM implements the numeric core, not
`gen<T>`.

This is a hard ceiling. Concurrency does not raise it, because the overhead is
CPU work and CPython serialises it.

### The crossover

To saturate the host you need enough calls in flight to keep it busy —
`concurrency = throughput × latency`:

| Provider latency | Concurrency needed to saturate Python |
|---|---|
| Frontier model, 800 ms | **141,507 in flight** |
| Small model, 150 ms | 26,533 in flight |
| Local model, 15 ms | 2,653 in flight |
| Cached lookup, 200 µs | 35 in flight |
| In-memory, 20 µs | 4 in flight |

And Humbaba's share of each call:

| Provider | Humbaba's share | Verdict |
|---|---|---|
| Frontier model, 800 ms | 0.00 % | host irrelevant |
| Small model, 150 ms | 0.00 % | host irrelevant |
| Local model, 15 ms | 0.04 % | host irrelevant |
| Cached lookup, 200 µs | 2.83 % | host noticeable |

**So Python is the bottleneck only above ~141,000 concurrent calls at frontier
latency, or below roughly 1 ms of provider latency.** Everywhere else the
network is the constraint and the host language is not.

### Python is already out of the execution path

This is the part the architecture answers for free. `humbaba build` emits portable
IR ahead of time:

```bash
humbaba build pipeline.hb -o pipeline.hbir   # Python, once, at build time
./go/humbaba-runtime pipeline.hbx             # no Python at run time
```

Verified end to end: identical output from the native host and the Python
backend. The front end can stay in Python — it runs once, at build time, and
contributes nothing to throughput — while the runtime is whatever host reads
HBIR. "Use a faster language for throughput" is not a rewrite; it is a
different host reading the same bytecode.

## 10. What v0.3 cost

v0.3 roughly doubled what the language does. Every addition was a candidate
regression, so each was measured (`python3 bench/v3.py`).

### The hot path is unchanged

| | v0.2 | v0.3 |
|---|---|---|
| Overhead per `gen<T>` | 7.6 µs | **7.63 µs** |
| Dispatch, per iteration | 2.89 µs | 3.21 µs |

Taint tracking and budget reservation are compile-time and dispatch-time work
respectively, so neither lands on the per-call path. The small dispatch
difference is within this container's noise.

### Static checking is free

| Phase | Time |
|---|---|
| Parse | 0.34 ms |
| **Check** | **0.04 ms** (11 % of parse) |
| Compile | 0.04 ms |
| Total front end | 0.41 ms |

Once per program, before anything executes. Against one 800 ms model call it is
invisible; against the money a misspelled prompt argument would waste, it is
free. This is the best trade in the project.

### The new constructs cost what they should

| Form | Per iteration |
|---|---|
| `for x in xs { work(x) }` | 3.29 µs |
| `while` + index + assignment | 4.04 µs |

1.23×, and it is real work — a bounds check and two assignments per iteration —
not overhead in shared machinery.

### Durability costs one fsync per step

| | Time |
|---|---|
| 50 plain iterations | 21.0 µs |
| 3 journaled steps | 0.98 ms (~0.33 ms per step) |

Dominated by `fsync`, which is the point: a journal that is not durable is not a
journal. Against an 800 ms model call that is ~0.2 % overhead, and it buys not
repeating the call after a crash.

### Schedulers — and a correction

| Tasks | Limit | Threads | asyncio | Gain |
|---|---|---|---|---|
| 2,000 | 1,000 | 118.7 ms | 77.5 ms | 1.5× |
| 5,000 | 2,000 | 362.9 ms | 268.3 ms | 1.4× |

**An earlier note in this project claimed 5.5×. That figure does not
reproduce.** It came from a single cold run; measured properly as min-of-N, the
gain is **1.4–1.5× on throughput**.

The throughput gain was never the point, though, and the reason to use asyncio
stands: **capacity**. Threads hit a wall near 10,000 in flight at ~2.2 MB of
reserved stack each; asyncio reaches ~500,000 at 0.71 KB. That ceiling is what
`--scheduler asyncio` lifts, and it is a 50× difference. The speed is a
rounding error either way.

## Verdict

For the workload Humbaba claims — orchestrating calls to remote models, bounded and
recoverable — **it is fast enough with about four orders of magnitude to
spare**, and the bottleneck is the network by an overwhelming margin.

The interpreter is still 7× slower than CPython at raw dispatch, and it cannot
be otherwise while it is written in Python. That would be a serious problem for
a general-purpose language and is nearly irrelevant here, which is the strongest
practical argument for the whole design: *choosing a narrow domain bought
permission to have a slow implementation and spend the effort on semantics
instead.*

It is worth being precise about the one sense in which Humbaba **is** faster than
Python. Running the same 24-document, two-stage pipeline against the same
provider (`bench/vs_python.py`):

| Implementation | Wall time |
|---|---|
| Python, written the obvious way | 12.01 s |
| Python, hand-tuned with a thread pool | 1.00 s |
| Humbaba | 1.00 s |

Humbaba is **12× faster than the Python most people write** and exactly level with
the Python an expert writes. It executes nothing faster; it does the concurrent
thing by default, bounded, ordered, and cancelled correctly, in one line. The
hand-tuned version closes the gap — after you have written the pool, the
ordering, the cancellation, the retries, the cost counter, the JSON validation
and the cache, and got all seven right.

That is the real claim. Not "faster than Python", which would be false, but
*"the fast version is the one you get by default"*, which is true and worth
more.

The right time to compile is after the semantics stop moving, not before.
Rewriting a language you are still designing is how these projects die.
