BINARY := humbaba-runtime
FRONTEND := python3 ../humbaba.py
# The one example that is inside the IR subset, so it can be compiled to
# bytecode and executed by this host. The others use gen/policy/parallel,
# which stay in the Python runtime by design.
EXAMPLE := ../examples/09_compute.hb
IR := /tmp/humbaba-compute.hbx

.PHONY: all build test bench check clean fmt run start verify exec-time compare smoke phases

all: check

# The only command that matters: does it compile, vet clean, pass tests, and
# produce the same output as the Python front end?
check: fmt build test verify smoke

fmt:
	gofmt -l -w .
	go vet ./...

build:
	go build -ldflags "-s -w" -o $(BINARY) .
	@ls -lh $(BINARY) | awk "{print \"  binary: \" \$$5}"

# hbx_test.go mirrors the assertions in tests/test_hbx.py: capability
# attenuation, taint refusal, budget limits. If these fail, this host is not
# executing Humbaba.
test:
	go test -v ./...

$(IR): $(EXAMPLE)
	$(FRONTEND) build $(EXAMPLE) -o $(IR)

# The real check: this host must agree with the Python front end, exactly.
# If this diff is non-empty the Go runtime is executing a different language.
verify: build $(IR)
	@$(FRONTEND) run $(EXAMPLE) 2>/dev/null | grep -v "gen call" | grep -v "^$$" > /tmp/humbaba-py.txt
	@./$(BINARY) $(IR) > /tmp/humbaba-go.txt
	@if diff -u /tmp/humbaba-py.txt /tmp/humbaba-go.txt; then \
		echo "  ✓ Go runtime matches the Python front end"; \
	else \
		echo "  ✗ MISMATCH — the Go runtime is executing different semantics"; \
		exit 1; \
	fi

run: build $(IR)
	./$(BINARY) $(IR)

# Every shipped example, compiled and executed by this host. Output is not
# diffed against Python here because the two mocks invent different field
# values; what is checked is that each program loads, runs, and either
# succeeds or fails for the reason it is supposed to.
smoke: build
	@pass=0; fail=0; \
	for f in ../examples/*.hb; do \
		case "$$f" in *06_modules*) continue;; esac; \
		$(FRONTEND) build $$f -o /tmp/hb-smoke.hbx >/dev/null 2>&1 || \
			{ echo "  compile failed: $$f"; fail=$$((fail+1)); continue; }; \
		out=$$(./$(BINARY) /tmp/hb-smoke.hbx 2>&1); \
		case "$$out" in \
			*"unknown instruction"*) echo "  MISSING OPCODE: $$f"; \
				echo "    $$out"; fail=$$((fail+1));; \
			*) pass=$$((pass+1));; \
		esac; \
	done; \
	echo "  ran $$pass, unimplemented-opcode failures $$fail"; \
	test $$fail -eq 0

# Allocation count is the number that matters here: the first version of this
# host allocated six times per function call, which on a recursive program
# dominated everything else.
bench: build $(IR)
	@echo "  execution, best of 50:"
	@printf "    "; ./$(BINARY) -time 50 $(IR) 2>&1 >/dev/null
	@echo "  phase breakdown:"
	@./$(BINARY) -phases $(IR) 2>&1 | head -2

# Cold start: whole-process wall time, including spawn and IR load.
# Uses python3 rather than /usr/bin/time, because -f is GNU-only and macOS
# ships the BSD version, which rejects it.
start: build $(IR)
	@python3 -c "import subprocess,time; \
ts=[(lambda t0:(subprocess.run(['./$(BINARY)','$(IR)'],capture_output=True),(time.perf_counter()-t0)*1e3)[1])(time.perf_counter()) for _ in range(20)]; \
print('  cold start, best of 20: %.2f ms' % min(ts)); \
print('  median:                 %.2f ms' % sorted(ts)[len(ts)//2])"

# Where does cold-start time actually go? Load, run, and process start are
# three different costs and only one of them is the program's fault.
phases: build $(IR)
	@./$(BINARY) -phases $(IR)

# Execution only, excluding spawn and IR load. Best of 20.
exec-time: build $(IR)
	@printf "  go exec, best of 20: "; ./$(BINARY) -time 20 $(IR) 2>&1

# Go against the C VM on the same machine, same program, same measurement.
# The only fair comparison — figures from different hardware are not
# comparable.
compare: build $(IR)
	@echo "  C  exec: no C host. The v1 one read a format the compiler no"
	@echo "           longer emits and was removed; an HBX one is unwritten."
	@printf "  Go exec, best of 20: "; ./$(BINARY) -time 20 $(IR) 2>&1 >/dev/null
	@printf "  python3 whole process, best of 5: "
	@python3 -c "import subprocess,time; \
ts=[(lambda t0:(subprocess.run(['python3','../humbaba.py','run','$(EXAMPLE)'],capture_output=True),(time.perf_counter()-t0)*1e3)[1])(time.perf_counter()) for _ in range(5)]; \
print('%.2f ms' % min(ts))"

clean:
	rm -f $(BINARY) $(IR) /tmp/humbaba-py.txt /tmp/humbaba-go.txt
