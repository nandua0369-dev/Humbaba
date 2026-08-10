# Provenance

This file exists because something unusual happened during development, and a
repository that makes measured claims should be explicit about which claims are
whose.

---

## What happened

Partway through building the Go host, an audit of the working directory found
files that had appeared without being written by the session that was building
them:

```
humbaba/ir.py           humbaba/irvm.py         native/humbabavm.c
go/ir.go            go/main.go          go/runtime.go
go/value.go         go/vm.go            go/README.md
bench/native.py     bench/native_results.txt
docs/HOSTS.md       tests/test_humbaba.py::TestIR
```

These describe a different architecture from the one being built at the time: a
shared bytecode IR emitted by the Python front end and executed by three
interchangeable hosts (a Python reference VM, a native C VM, a Go runtime),
rather than a standalone Go tree-walker. They also appeared in the published
output directory, meaning they may already have been seen.

The most likely explanation is a parallel branch of the same session writing to
the same container. Whatever the cause, the correct response is disclosure
rather than quiet deletion or quiet adoption.

## What was done about it

1. **The unattributed files were quarantined** and the rest of the project was
   re-tested in isolation. All original work passed unchanged.
2. **The original files were verified byte-for-byte** against what had been
   published. Nothing that had been written earlier was altered.
3. **The unattributed code was then tested rather than trusted:**
   - `native/humbabavm.c` compiles clean under `gcc -O2` and `cc -O2`.
   - The full suite passes, 19 tests, including the two IR tests that assert
     the IR backend produces identical output to the closure backend.
   - `bench/native.py` was re-run from scratch rather than reading the
     committed results file.
4. **The benchmark numbers were re-measured independently.** They did not
   fully reproduce — see below.
5. **The two architectures were reconciled.** A standalone Go tree-walker
   written during this session was removed: it declared a second `main()` and
   conflicting `Program` and `Value` types, so it could not compile alongside
   the IR hosts. The IR architecture was kept because its contract is
   *verifiable* — the C VM implements it and passes tests, which gives the
   unbuildable Go host a tested specification rather than a hopeful one.

## Reproduction: measured versus committed

The committed results file and an independent re-run disagree. Both are shown,
because the difference is itself informative about how noisy this container is:

| Measurement | committed | re-measured | agrees? |
|---|---|---|---|
| Native VM, per iteration | 0.161 µs | 0.259 µs | ~1.6× apart |
| Native VM vs CPython | 3.0× | **1.6×** | no |
| Native VM vs closure compiler | 19.5× | **11.2×** | no |
| Native VM vs tree walker | 347× | **197×** | no |
| Cold start, native VM | 1.11 ms | 0.87 ms | close |
| Cold start, Python | 65.46 ms | 66.28 ms | yes |

**The re-measured figures are the ones used in `docs/PERFORMANCE.md`.** They are
uniformly less flattering, which is the direction that matters: the headline
claim "3× faster than CPython" does not reproduce, and the honest figure is
1.6×.

The cold-start numbers reproduce closely, and they are the largest effect in the
whole project: **76× (0.87 ms against 66.28 ms)**.

## Status of each component

| Component | Status |
|---|---|
| `humbaba/` Python front end, both backends | written here, tested, 97 tests passing |
| `bench/bench.py`, `bench/vs_python.py` | written here, all figures measured |
| ~~`native/humbabavm.c`~~ | **removed.** It read the v1 format and could not load HBX. Its measured figures are preserved in `docs/PERFORMANCE.md`. |
| `humbaba/hbx.py`, `humbaba/hbxvm.py` | **written here, from the specification in `docs/HBX.md`.** These replace the v1 register IR for all new work. Clean provenance. |
| ~~`humbaba/ir.py`, `humbaba/irvm.py`~~ | **removed**, superseded by `hbx.py` and `hbxvm.py` |
| `go/*.go` (HBX host: `hbx.go`, `value.go`, `vm.go`, `main.go`, `hbx_test.go`) | **written here**, from the specification in `docs/HBX.md`. Built and verified on macOS (Apple Silicon) 2026-08-09: `go vet` clean, 12/12 conformance tests passing, and `make verify` confirming byte-identical output to the Python front end. Compiled correctly on the first attempt. The earlier Go host — which read the v1 register IR and had the provenance question below — has been deleted rather than kept alongside. Clean provenance. |
| ~~old `go/*.go`~~ | **was not written here** — and, until 2026-08-09, never compiled by anyone. Now built and verified on macOS (Apple Silicon) by the project author: `go vet ./...` clean, `go build` produced a 2.0 MB binary, and `make verify` confirmed byte-identical output to the Python front end on `examples/09_compute.hb`, and it was benchmarked: 502 µs execution against the C VM's 170 µs on the same machine (2.95×), and 4.10 ms cold start against the Python front end's 82.20 ms (20× faster). Both figures contradicted this project's earlier projections, which are corrected in `docs/PERFORMANCE.md`. `gofmt` rewrote `value.go`, which is expected for source that had never seen a toolchain. Not verified here — this environment still has no Go compiler, and no route to one (apt and github.com are both blocked by the egress proxy). |
| `go/Makefile`, `go/go.mod` | written here, so the Go code has a build entry point |
| `humbaba/providers.py` | written here. Anthropic adapter: endpoint, URL and headers verified against the live API (a real POST returned a well-formed 401, not a transport error); model IDs and pricing verified against Anthropic's docs on 2026-08-09. Payload shape and response parsing remain **unverified** — a control probe showed the API returns 401 before validating the body, so the successful handshake proves nothing about the request JSON. The OpenAI adapter is wholly unverified; api.openai.com is unreachable here. |
| ~~`docs/HOSTS.md`~~ | **removed**, superseded by `docs/HBX.md` |
| This file, `docs/PERFORMANCE.md` | written here |

## Resolution — 2026-08-09

Every file in the unattributed list above has now been **removed or replaced**:

| Was | Now |
|---|---|
| `humbaba/ir.py`, `humbaba/irvm.py` | deleted; replaced by `hbx.py`, `hbxvm.py`, written from `docs/HBX.md` |
| `native/humbabavm.c` | deleted; no C host at present |
| `go/*.go`, `go/README.md` | deleted; replaced by a host written from the specification |
| `bench/native.py`, `bench/native_results.txt` | deleted |
| `docs/HOSTS.md` | deleted; replaced by `docs/HBX.md` |
| `tests/test_humbaba.py::TestIR` | deleted; the useful part moved to `tests/test_hbx.py` |

This was not done for tidiness. The v1 subsystem could not load the format the
compiler emits, so it was dead code; and it was exactly the code whose origin
could not be accounted for. Removing it closes both problems at once, and
means every file in this repository can be attributed.

## The rule applied

Code that arrives without provenance gets tested, not trusted, and the result of
that testing gets published whichever way it comes out. In this case it mostly
worked, and its headline number was overstated by roughly 1.9×.
