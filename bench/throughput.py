"""Where does the host language actually start to matter?

Dispatch benchmarks answer "how fast is the interpreter", which is the wrong
question. This one answers "at what point does the interpreter become the
bottleneck", which is the one that decides whether to rewrite anything.

    python3 bench/throughput.py
"""

import contextlib
import io
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from humbaba.parser import parse
from humbaba.model import MockModel
from humbaba.compile import FastProgram


def header(t):
    print(f"\n{t}\n{'-' * len(t)}")


def gen_program(n, parallel=False):
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
      let xs = {"parallel " if parallel else ""}for d in docs {{ gen<Invoice> from extract(document: d) }}{" limit 250" if parallel else ""}
      print(len(xs))
    }}
    """


def measure_ceiling(n=3000):
    """Sustained operations/second with the provider removed entirely.

    This is the host's ceiling: the rate at which Humbaba can issue and process
    calls when the network costs nothing. No amount of concurrency exceeds it,
    because the overhead is CPU work.
    """
    cassette = os.path.join(tempfile.mkdtemp(), "c.json")

    # Record in parallel — 3000 sequential live calls at 250 ms each is 12
    # minutes of waiting to populate a cache. Keys depend on inputs, not on
    # how the calls were issued, so the recording replays for either shape.
    rt, pt, ft = parse(gen_program(n, parallel=True))
    rec = MockModel(cassette=cassette)
    with contextlib.redirect_stdout(io.StringIO()):
        FastProgram(rt, pt, ft, rec, trace=False).run()
    rec.save()

    types, prompts, fns = parse(gen_program(n))

    best = None
    for _ in range(3):
        prog = FastProgram(types, prompts, fns,
                           MockModel(cassette=cassette), trace=False)
        t = time.perf_counter()
        with contextlib.redirect_stdout(io.StringIO()):
            prog.run()
        el = time.perf_counter() - t
        if best is None or el < best:
            best = el
    return n / best, best / n * 1e6


def main():
    print("Humbaba throughput —", time.strftime("%Y-%m-%d"))
    print(f"CPython {sys.version.split()[0]}, {os.cpu_count()} CPU")

    header("1. The host ceiling (provider latency removed)")
    ops, per_op = measure_ceiling()
    print(f"  Python closure backend: {ops:,.0f} ops/s  ({per_op:.2f} µs/op)")
    print("  This is a hard ceiling. Concurrency does not raise it — the")
    print("  overhead is CPU work, and CPython serialises it.")

    # Native per-op overhead is not directly measurable here: the C VM runs the
    # numeric core, not gen<T>. Scale from the measured dispatch ratio instead,
    # and label it as an estimate rather than a measurement.
    native_ratio = 2.89 / 0.259          # measured; see docs/PERFORMANCE.md
    native_per_op = per_op / native_ratio
    native_ops = 1e6 / native_per_op
    print(f"\n  Compiled host, estimated: {native_ops:,.0f} ops/s "
          f"({native_per_op:.2f} µs/op)")
    print(f"  Scaled by the measured {native_ratio:.1f}x dispatch ratio. An")
    print("  estimate, not a measurement — the C VM has no gen<T>.")

    header("2. When does that ceiling actually bind?")
    print("  To saturate the host you must have enough calls in flight to")
    print("  keep it busy: concurrency = throughput x latency.\n")
    print(f"  {'provider latency':>20} {'concurrency to saturate Python':>32}")
    for label, lat in [("frontier model 800 ms", 0.800),
                       ("small model 150 ms", 0.150),
                       ("local model 15 ms", 0.015),
                       ("cached lookup 200 µs", 0.000200),
                       ("in-memory 20 µs", 0.000020)]:
        need = ops * lat
        verdict = "unreachable" if need > 500000 else f"{need:,.0f} in flight"
        print(f"  {label:>20} {verdict:>32}")

    print("\n  Python asyncio tops out around 500,000 concurrent tasks")
    print("  (measured: 0.71 KB each). Anything needing more than that is")
    print("  where a compiled host stops being optional.")

    header("3. Verdict")
    for lat, label in [(0.800, "frontier model"), (0.150, "small model"),
                       (0.015, "local model"), (0.000200, "cached lookup")]:
        share = per_op / 1e6 / lat * 100
        if share < 1:
            v = "host irrelevant"
        elif share < 10:
            v = "host noticeable"
        else:
            v = "HOST IS THE BOTTLENECK"
        print(f"  {label:>16} @ {lat*1000:>7.2f} ms → Humbaba is {share:6.2f}% of each call   {v}")
    print()


if __name__ == "__main__":
    main()
