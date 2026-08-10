"""Humbaba vs Python, on the same workload, against the same provider.

Two separate questions, deliberately kept apart:

  A. Is Humbaba faster than Python at executing code?      No. Not close.
  B. Is Humbaba faster than Python at the job it exists for? Yes, and the reason
     has nothing to do with dispatch speed.

    python3 bench/vs_python.py
"""

import contextlib
import io
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from humbaba.parser import parse
from humbaba.model import MockModel
from humbaba.compile import FastProgram

DOCS = [f"INVOICE from Vendor{i} Ltd. Amount due: {(i + 1) * 137}.00 GBP"
        for i in range(24)]
SCHEMA = (("vendor", "string"), ("total", "number"))
SYSTEM = "Extract the vendor and the total amount due."


def header(t):
    print(f"\n{t}\n{'-' * len(t)}")


_PROG_CACHE = {}


def run_humbaba(src):
    """Times execution only. Parse and compile happen once, outside the clock."""
    prog = _PROG_CACHE.get(src)
    if prog is None:
        types, prompts, fns = parse(src)
        prog = _PROG_CACHE[src] = FastProgram(
            types, prompts, fns, MockModel(), trace=False)
    t = time.perf_counter()
    with contextlib.redirect_stdout(io.StringIO()):
        prog.run()
    return time.perf_counter() - t


# ---------------------------------------------------------------- A. dispatch

def bench_raw_dispatch():
    header("A. Raw execution speed — Humbaba loses, and should")

    N = 20000
    terms = " + ".join(["x * 2 - 1"] * 10)
    humbaba_src = f"""
    fn work(x: number) -> number {{ return {terms} }}
    fn main() {{
      let xs = [{", ".join(str(i) for i in range(N))}]
      let ys = for x in xs {{ work(x) }}
      print(len(ys))
    }}"""
    humbaba = min(run_humbaba(humbaba_src) for _ in range(3))

    def work(x):
        return (x * 2 - 1) + (x * 2 - 1) + (x * 2 - 1) + (x * 2 - 1) + (x * 2 - 1) \
             + (x * 2 - 1) + (x * 2 - 1) + (x * 2 - 1) + (x * 2 - 1) + (x * 2 - 1)

    xs = list(range(N))

    def pyrun():
        t = time.perf_counter()
        [work(x) for x in xs]
        return time.perf_counter() - t

    py = min(pyrun() for _ in range(3))

    print(f"  {N} iterations of an identical function")
    print(f"  Humbaba (compiled closures) : {humbaba * 1e3:8.1f} ms  ({humbaba / N * 1e6:5.2f} µs/iter)")
    print(f"  CPython                 : {py * 1e3:8.1f} ms  ({py / N * 1e6:5.2f} µs/iter)")
    print(f"  → CPython is {humbaba / py:.1f}x faster. An interpreter written in Python")
    print("    cannot beat Python; every Humbaba op costs ≥1 Python op.")


# ---------------------------------------------------------------- B. workload

HUMBABA_PIPELINE = """
type Invoice { vendor: string  total: number }
type Verdict { text: string }

prompt extract(document: untrusted string) {
  system: "Extract the vendor and the total amount due."
  user:   "Document: {document}"
}
prompt classify(vendor: untrusted string) {
  system: "Classify this vendor."
  user:   "Vendor: {vendor}"
}

fn main() uses { model } budget { max: 5.00 } {
  let docs = [%s]
  let out = parallel for d in docs {
    gen<Verdict> from classify(vendor: gen<Invoice> from extract(document: d).vendor)
  } limit 16
  print(len(out))
}
"""


def py_call(model, system, user):
    """Exactly what Humbaba does per call, written out by hand."""
    return model.generate("large", system, user, SCHEMA)


def bench_naive_python():
    model = MockModel()
    t = time.perf_counter()
    out = []
    for d in DOCS:
        inv, _ = py_call(model, SYSTEM, f"Document: {d}")
        v, _ = model.generate("large", "Classify this vendor.",
                              f"Vendor: {inv['vendor']}", (("text", "string"),))
        out.append(v)
    return time.perf_counter() - t


def bench_expert_python():
    model = MockModel()

    def one(d):
        inv, _ = py_call(model, SYSTEM, f"Document: {d}")
        v, _ = model.generate("large", "Classify this vendor.",
                              f"Vendor: {inv['vendor']}", (("text", "string"),))
        return v

    t = time.perf_counter()
    with ThreadPoolExecutor(max_workers=16) as pool:
        list(pool.map(one, DOCS))
    return time.perf_counter() - t


def bench_workload():
    header("B. The actual workload — 24 documents, 2 chained calls each")

    docs_lit = ", ".join(f'"{d}"' for d in DOCS)
    humbaba = run_humbaba(HUMBABA_PIPELINE % docs_lit)
    naive = bench_naive_python()
    expert = bench_expert_python()

    print(f"  Python, written the obvious way : {naive:6.2f} s")
    print(f"  Python, hand-tuned with a pool  : {expert:6.2f} s")
    print(f"  Humbaba                             : {humbaba:6.2f} s")
    print()
    print(f"  → Humbaba is {naive / humbaba:5.1f}x faster than the obvious Python")
    print(f"  → Humbaba is {expert / humbaba:5.1f}x the hand-tuned Python (parity is the goal)")
    print()
    print("  Humbaba is not executing anything faster. It is doing the concurrent")
    print("  thing by default, where Python does the sequential thing by default.")
    print("  The hand-tuned version closes the gap — and that is the point:")
    print("  you had to write it, and remember to bound it, and get it right.")


# ---------------------------------------------------------------- C. effort

def bench_effort():
    header("C. What the equivalent programs cost to write")
    humbaba_body = """let out = parallel for d in docs {
  gen<Verdict> from classify(vendor: gen<Invoice> from extract(document: d).vendor)
} limit 16"""
    print("  Humbaba, complete:")
    for line in humbaba_body.splitlines():
        print(f"      {line}")
    print()
    print("  The hand-tuned Python needs, separately and by hand:")
    for item in [
        "a ThreadPoolExecutor and a chosen width",
        "ordered collection of results",
        "cancellation on failure, or orphaned work",
        "retry logic, and the hard/soft distinction",
        "a cost counter, checked somewhere",
        "JSON parsing and shape validation",
        "delimiting untrusted text in the prompt",
        "a cache, if you want replay",
    ]:
        print(f"      · {item}")
    print()
    print("  Every one of those is a place to be wrong. Humbaba's speed advantage")
    print("  over ordinary Python is really a default-correctness advantage.")


def main():
    print("Humbaba vs Python —", time.strftime("%Y-%m-%d"))
    print(f"CPython {sys.version.split()[0]}")
    bench_raw_dispatch()
    bench_workload()
    bench_effort()
    print()


if __name__ == "__main__":
    main()
