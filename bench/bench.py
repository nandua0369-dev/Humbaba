"""Humbaba v0.1 benchmarks.

Measures the runtime's own cost, in isolation from model latency, then asks
the only question that matters: what fraction of a real request is Humbaba?

    python3 bench/bench.py
"""

import os
import resource
import statistics
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from humbaba.lexer import tokenize
from humbaba.parser import parse
from humbaba.model import MockModel
from humbaba.runtime import Interpreter
from humbaba.compile import FastProgram

TMP = tempfile.mkdtemp()


def proc_mb(field):
    try:
        for line in open("/proc/self/status"):
            if line.startswith(field):
                return int(line.split()[1]) / 1024.0
    except OSError:
        pass
    return float("nan")


def rss_mb():
    return proc_mb("VmRSS")


BACKEND = os.environ.get("HUMBABA_BACKEND", "fast")


def build(src, model=None, backend=None):
    """Parse and compile, returning something ready to run.

    Kept separate from run_src so benchmarks can exclude front-end cost —
    parsing a 20,000-element list literal is not interpreter dispatch.
    """
    types, prompts, fns = parse(src)
    cls = FastProgram if (backend or BACKEND) == "fast" else Interpreter
    return cls(types, prompts, fns, model or MockModel(), trace=False)


def run_src(src, model=None, entry="main", backend=None):
    return build(src, model, backend).run(entry)


def timed(fn, repeats=5):
    samples = []
    for _ in range(repeats):
        t = time.perf_counter()
        fn()
        samples.append(time.perf_counter() - t)
    return min(samples), statistics.median(samples)


def _time(fn):
    t = time.perf_counter()
    fn()
    return time.perf_counter() - t


def header(title):
    print(f"\n{title}\n{'-' * len(title)}")


# ---------------------------------------------------------------- 1. front end

SAMPLE = """
type Invoice { vendor: string  total: number }
prompt extract(document: untrusted string) {
  system: "Extract the vendor and the total amount due."
  user:   "Document: {document}"
}
fn helper(x: number) -> number { return x * 2 + 1 }
fn main() uses { model } budget { max: 0.50 } {
  let doc = "INVOICE from Acme Robotics. Amount due: 2400.00 GBP"
  policy { retry: 3, fallback: "small" } {
    let inv = gen<Invoice> from extract(document: doc)
    print(inv.vendor, helper(inv.total))
  }
}
""" * 8   # ~130 lines of declarations


def bench_frontend():
    header("1. Front end")
    src = SAMPLE.replace("main()", "main0()", 1)  # avoid duplicate-name confusion
    ntok = len(tokenize(SAMPLE))
    lex_min, _ = timed(lambda: tokenize(SAMPLE), 20)
    print(f"  lex      {ntok:>6} tokens in {lex_min * 1e3:7.2f} ms "
          f"({ntok / lex_min / 1e6:.1f}M tokens/s)")
    # parse() rejects duplicate decls only by overwriting, so this is safe
    parse_min, _ = timed(lambda: parse(SAMPLE), 20)
    print(f"  parse    {SAMPLE.count(chr(10)):>6} lines in {parse_min * 1e3:7.2f} ms")
    print(f"  → a 10,000-line program parses in ~{parse_min * 1e3 * 10000 / SAMPLE.count(chr(10)):.0f} ms")


# ---------------------------------------------------------------- 2. dispatch

def bench_dispatch():
    header("2. Interpreter dispatch (no model involved)")
    N = 20000
    body = " + ".join(["x * 2 - 1"] * 10)
    src = f"""
    type T {{ v: number }}
    fn work(x: number) -> number {{ return {body} }}
    fn main() {{
      let xs = [{", ".join(str(i) for i in range(N))}]
      let ys = for x in xs {{ work(x) }}
      print(len(ys))
    }}
    """
    # this container is noisy; min-of-5, interleaved to cancel drift.
    # Front-end cost is excluded: build first, then time only execution.
    progs = {b: build(src, backend=b) for b in ("tree", "fast")}
    results = {"tree": [], "fast": []}
    for _ in range(5):
        for backend in ("tree", "fast"):
            t = time.perf_counter()
            progs[backend].run()
            results[backend].append((time.perf_counter() - t) / N)
    results = {k: min(v) for k, v in results.items()}
    print(f"  {N} iterations, ~14 AST nodes each")
    for backend in ("tree", "fast"):
        per = results[backend]
        print(f"  {backend:>5}: {per * 1e6:6.2f} µs/iter  "
              f"≈ {1 / per:>9,.0f} fn-calls/s  "
              f"≈ {14 / per / 1e6:5.1f}M AST nodes/s")
    print(f"  → closure compilation is {results['tree'] / results['fast']:.1f}x "
          f"faster than tree-walking")


# ---------------------------------------------------------------- 3. per-gen

GEN_DOC = "INVOICE from Acme Robotics. Amount due: 2400.00 GBP"


def gen_program(n, limit, unique=True):
    if unique:
        docs = ", ".join(f'"INVOICE from Vendor{i}. Amount due: {i}00.00 GBP"'
                         for i in range(n))
    else:
        docs = ", ".join([f'"{GEN_DOC}"'] * n)
    return f"""
    type Invoice {{ vendor: string  total: number }}
    prompt extract(document: untrusted string) {{
      system: "Extract the vendor and the total amount due."
      user:   "Document: {{document}}"
    }}
    fn main() uses {{ model }} {{
      let docs = [{docs}]
      let xs = parallel for d in docs {{ gen<Invoice> from extract(document: d) }} limit {limit}
      print(len(xs))
    }}
    """


def bench_gen_overhead():
    header("3. Runtime overhead per gen<T> call")
    cassette = os.path.join(TMP, "bench.json")
    # record once
    N = 2000
    # record all N distinct responses in parallel; keys depend only on inputs
    m = MockModel(cassette=cassette)
    run_src(gen_program(N, 250), m)
    m.save()
    gsrc = gen_sequential(N)
    per = {"tree": [], "fast": []}
    for _ in range(3):
        for backend in ("tree", "fast"):
            prog = build(gsrc, MockModel(cassette=cassette), backend)
            t = time.perf_counter()
            prog.run()
            per[backend].append((time.perf_counter() - t) / N)
    per = {k: min(v) for k, v in per.items()}
    print(f"  {N} replayed gens (fencing, hashing, coercion, budget, policy)")
    for backend in ("tree", "fast"):
        print(f"  {backend:>5}: {per[backend] * 1e6:5.1f} µs per gen")
    per = per["fast"]
    for latency, label in [(0.300, "fast model"), (0.800, "typical"), (8.0, "long agent step")]:
        print(f"  → against a {label} at {latency * 1e3:.0f} ms: "
              f"Humbaba is {per / latency * 100:.4f}% of wall time")


# ---------------------------------------------------------------- 4. scaling

def gen_sequential(n):
    docs = ", ".join(f'"INVOICE from Vendor{i}. Amount due: {i}00.00 GBP"'
                     for i in range(n))
    return f"""
    type Invoice {{ vendor: string  total: number }}
    prompt extract(document: untrusted string) {{
      system: "Extract the vendor and the total amount due."
      user:   "Document: {{document}}"
    }}
    fn main() uses {{ model }} {{
      let docs = [{docs}]
      let xs = for d in docs {{ gen<Invoice> from extract(document: d) }}
      print(len(xs))
    }}
    """


def bench_task_overhead():
    header("4. Cost of the parallel machinery itself")
    N = 5000
    xs = ", ".join(str(i) for i in range(N))
    seq = f"fn main() {{ let ys = for x in [{xs}] {{ x }} print(len(ys)) }}"
    par = f"fn main() {{ let ys = parallel for x in [{xs}] {{ x }} limit 8 print(len(ys)) }}"
    pseq, ppar = build(seq), build(par)
    a = min(_time(pseq.run) for _ in range(3))
    b = min(_time(ppar.run) for _ in range(3))
    print(f"  sequential {N} trivial iterations: {a * 1e3:7.1f} ms")
    print(f"  parallel   {N} trivial iterations: {b * 1e3:7.1f} ms")
    print(f"  → dispatching a task costs ~{(b - a) / N * 1e6:.0f} µs "
          f"(vs ~0.3 µs for a goroutine)")


def bench_concurrency():
    header("5. Concurrency scaling (I/O-bound, 250 ms simulated latency)")
    N = 128
    baseline = None
    for limit in (16, 64, 128):
        m = MockModel()
        t = time.perf_counter()
        run_src(gen_program(N, limit), m)
        elapsed = time.perf_counter() - t
        ideal = 0.25 * (N / limit)
        if baseline is None:
            baseline = elapsed * limit
        print(f"  limit {limit:>4}: {elapsed:6.2f} s  (ideal {ideal:.2f} s, "
              f"efficiency {ideal / elapsed * 100:5.1f}%)")
    print(f"  serial would be {0.25 * N:.1f} s")


def bench_cpu_bound():
    header("6. Concurrency scaling (CPU-bound — the GIL test)")
    N = 512
    body = " + ".join(["x * 2 - 1"] * 40)
    for limit in (1, 8):
        src = f"""
        fn work(x: number) -> number {{ return {body} }}
        fn main() {{
          let xs = [{", ".join(str(i) for i in range(N))}]
          let ys = parallel for x in xs {{ work(x) }} limit {limit}
          print(len(ys))
        }}
        """
        t = time.perf_counter()
        run_src(src)
        print(f"  limit {limit:>2}: {(time.perf_counter() - t) * 1e3:7.1f} ms")
    if os.cpu_count() == 1:
        print("  NOTE: this machine reports 1 CPU, so this measures nothing about")
        print("  the GIL. CPython threads serialise on CPU-bound work regardless;")
        print("  treat that as a known property, not as something measured here.")
    else:
        print("  no speedup expected: CPython threads serialise on the GIL")


# ---------------------------------------------------------------- 6. capacity

def bench_thread_capacity():
    header("7. In-flight task capacity (threads)")
    base_rss, base_vm = rss_mb(), proc_mb("VmSize")
    print(f"  baseline: RSS {base_rss:.0f} MB, virtual {base_vm:.0f} MB")
    for n in (100, 1000, 5000, 10000):
        t = time.perf_counter()
        try:
            with ThreadPoolExecutor(max_workers=n) as pool:
                futs = [pool.submit(time.sleep, 0.05) for _ in range(n)]
                peak_rss, peak_vm = rss_mb(), proc_mb("VmSize")
                for f in futs:
                    f.result()
            spawn = time.perf_counter() - t
            print(f"  {n:>6} live threads: {spawn * 1e3:8.1f} ms, "
                  f"RSS +{peak_rss - base_rss:6.1f} MB, "
                  f"virtual +{peak_vm - base_vm:8.0f} MB "
                  f"({(peak_vm - base_vm) / n * 1024:.0f} KB/thread)")
        except (RuntimeError, MemoryError, OSError) as e:
            print(f"  {n:>6} live threads: FAILED — {type(e).__name__}: {e}")
            break
    print("  goroutines start at ~2 KB; OS threads at ~8 MB of reserved stack.")
    print("  This is the hard ceiling on the blueprint's '100,000 in flight'.")


def main():
    print("Humbaba v0.1 benchmarks —", time.strftime("%Y-%m-%d"))
    print(f"CPython {sys.version.split()[0]}, {os.cpu_count()} CPUs")
    bench_frontend()
    bench_dispatch()
    bench_gen_overhead()
    bench_task_overhead()
    bench_concurrency()
    bench_cpu_bound()
    bench_thread_capacity()
    print()


if __name__ == "__main__":
    main()
