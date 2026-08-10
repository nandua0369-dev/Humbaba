# The Go host

A Humbaba runtime that executes HBX. One binary, no Python, no dependencies
beyond the Go standard library.

```bash
make check
```

That runs `gofmt`, `go vet`, `go build`, `go test`, and then the part that
matters — `verify`, which runs the same program through the Python front end
and this binary and diffs the output.

## What this host enforces

It is not enough to compute the right numbers. A conforming host must refuse
the right things, and this one does:

- **Capability attenuation.** `CALL` intersects the callee's declared
  capabilities with the caller's actual set, so authority only ever shrinks.
- **Taint propagation.** Every value carries a taint bit; every operation
  propagates it. `REQUIRE` refuses a tainted operand unless the instruction
  carries the allow-tainted flag, which the compiler emits only for an
  explicit written-reason unwrap.
- **Fencing.** `GEN` wraps tainted arguments in nonce-delimited markers before
  they reach a model, and appends the third-party-data notice to the system
  prompt. The caller cannot skip it.
- **Budgets.** Charges walk to the root of the budget tree, checking every
  capped ancestor before committing to any, so a refused charge leaves no
  partial state.
- **Policy.** Retry and fallback carried on the `GEN` instruction, with
  transient failures and refusals handled differently.

`hbx_test.go` asserts each of these. They mirror `tests/test_hbx.py`, and if
the two hosts disagree, one of them is running a different language.

## Files

| File | Contents |
|---|---|
| `hbx.go` | the HBX loader — strict, because a host that guesses runs a different program |
| `value.go` | the value representation, with taint as a field rather than a wrapper |
| `vm.go` | the interpreter and every enforcement rule |
| `main.go` | CLI and a deterministic mock model |
| `hbx_test.go` | conformance assertions |

## Usage

```bash
python3 ../humbaba.py build ../examples/09_compute.hb -o /tmp/p.hbx
./humbaba-runtime /tmp/p.hbx
./humbaba-runtime -time 20 /tmp/p.hbx     # best execution time of 20
./humbaba-runtime -entry other /tmp/p.hbx
```

## Cross-compiling

One machine, every target:

```bash
GOOS=linux  GOARCH=amd64 go build -o dist/humbaba-linux-amd64  .
GOOS=linux  GOARCH=arm64 go build -o dist/humbaba-linux-arm64  .
GOOS=darwin GOARCH=arm64 go build -o dist/humbaba-macos-arm64  .
GOOS=windows GOARCH=amd64 go build -o dist/humbaba-windows.exe .
```

## Honest limits

**The mock model is not the Python mock.** It is deterministic and keyed on
the prompt inputs, but it does not reproduce the Python `MockModel`'s
cassettes, chaos injection, or injection-obedience simulation. Equivalence
between the two hosts is claimed for **enforcement** — capability refusals,
taint refusals, budget stops, and the compute subset — not for the field
values a mock invents.

**No provider adapters.** This host has no HTTP client. Running against a real
model means implementing the `Model` interface, which is three methods' worth
of work and deliberately left out until someone needs it.

**`policy` works.** Retry and fallback are operands on `GEN`, resolved from
the enclosing block at compile time, and this host implements both. It
distinguishes a transient failure (no answer — retry) from a refusal (an
answer that did not match the declared type — try the fallback model first).

**Durable steps journal.** Pass `-journal <dir>` and each `step` appends its
result, fsync'd, before moving on. A restarted run replays completed steps
without executing them, and the spend recorded before the crash is restored so
a resumed run cannot exceed its budget by counting from zero again. The file
format matches the Python host's, though resuming a run *started by the other
host* is untested.
