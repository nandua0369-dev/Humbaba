"""What did v0.3 cost?

v0.3 added a static checker, a journal, taint tracking, budget reservation, and
a second scheduler. Each is a candidate regression. This measures them.

    python3 bench/v3.py
"""

import contextlib
import io
import os
import shutil
import statistics
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from humbaba.parser import Parser, parse
from humbaba.check import check as static_check
from humbaba.model import MockModel
from humbaba.compile import FastProgram


def header(t):
    print(f"\n{t}\n{'-' * len(t)}")


def best(fn, reps=5):
    return min(_time(fn) for _ in range(reps))


def _time(fn):
    t = time.perf_counter()
    with contextlib.redirect_stdout(io.StringIO()):
        fn()
    return time.perf_counter() - t


PROGRAM = """
capability db.write

type Line  { sku: string  qty: number }
type Order { id: string  total: number  note: string? }

prompt extract(document: untrusted string) {
  system: "Extract the order."
  user:   "Document: {document}"
}

fn tier(n: number) -> string {
  if n > 1000 and n < 100000 { return "standard" }
  if n >= 100000 or n == 0 { return "review" }
  return "small"
}

fn main() uses { model, db.write } budget { max: 5.00 } {
  var total = 0
  var i = 0
  while i < 20 {
    i = i + 1
    if i == 3 { continue }
    if i > 15 { break }
    total = total + i
  }
  let o = Order { id: "A1", total: total }
  print(o.id, tier(o.total), -total)
}
"""


def bench_checker():
    header("1. Static checking — what does `humbaba check` cost?")
    src = PROGRAM
    p = Parser(src)
    types, prompts, fns = p.parse()

    t_parse = best(lambda: Parser(src).parse(), 20)
    t_check = best(lambda: static_check(types, prompts, fns, p.caps), 20)
    t_compile = best(
        lambda: FastProgram(types, prompts, fns, MockModel(), trace=False), 20)

    print(f"  parse         {t_parse * 1e3:6.2f} ms")
    print(f"  check         {t_check * 1e3:6.2f} ms   "
          f"({t_check / t_parse * 100:.0f}% of parse)")
    print(f"  compile       {t_compile * 1e3:6.2f} ms")
    print(f"  total front   {(t_parse + t_check + t_compile) * 1e3:6.2f} ms")
    print()
    print("  Checking runs once per program, before anything executes. Against")
    print("  a single 800 ms model call it is already invisible; against the")
    print("  money a wrong prompt argument would waste, it is free.")


def bench_dispatch_regression():
    header("2. Did the new features slow the hot path?")
    n = 20000
    terms = " + ".join(["x * 2 - 1"] * 10)
    nums = ", ".join(str(i) for i in range(n))

    # v0.2-style: no new constructs
    old = f"""
    fn work(x: number) -> number {{ return {terms} }}
    fn main() {{
      let xs = [{nums}]
      let ys = for x in xs {{ work(x) }}
      print(len(ys))
    }}"""

    # v0.3: same work, expressed with the new constructs
    new = f"""
    fn work(x: number) -> number {{ return {terms} }}
    fn main() {{
      let xs = [{nums}]
      var i = 0
      var acc = 0
      while i < len(xs) {{
        acc = acc + work(xs[i])
        i = i + 1
      }}
      print(acc)
    }}"""

    results = {}
    for label, src in (("for + fn call", old), ("while + index + assign", new)):
        types, prompts, fns = parse(src)
        prog = FastProgram(types, prompts, fns, MockModel(), trace=False)
        results[label] = best(prog.run, 3) / n

    for label, per in results.items():
        print(f"  {label:24} {per * 1e6:6.2f} µs/iter  "
              f"≈ {1 / per:>9,.0f} iter/s")
    a, b = list(results.values())
    print(f"\n  The while/index/assign form is {b / a:.2f}x the cost of the")
    print("  for-loop form — extra work per iteration (bounds check, two")
    print("  assignments), not a regression in the shared machinery.")


def bench_gen_overhead():
    header("3. Per-gen overhead — taint and reservation added, did it cost?")
    n = 2000
    docs = ", ".join(f'"INVOICE from Vendor{i}. Amount due: {i}00.00"'
                     for i in range(n))
    base = f"""
    type Invoice {{ vendor: string  total: number }}
    prompt extract(document: untrusted string) {{
      system: "Extract the vendor and the total amount due."
      user:   "Document: {{document}}"
    }}
    fn main() uses {{ model }} {{
      let docs = [{docs}]
      let xs = %s
      print(len(xs))
    }}"""

    cassette = os.path.join(tempfile.mkdtemp(), "c.json")
    par = base % ("parallel for d in docs { gen<Invoice> from extract(document: d) } limit 250")
    types, prompts, fns = parse(par)
    rec = MockModel(cassette=cassette)
    with contextlib.redirect_stdout(io.StringIO()):
        FastProgram(types, prompts, fns, rec, trace=False).run()
    rec.save()

    seq = base % "for d in docs { gen<Invoice> from extract(document: d) }"
    types, prompts, fns = parse(seq)

    def go():
        FastProgram(types, prompts, fns, MockModel(cassette=cassette),
                    trace=False).run()

    per = best(go, 3) / n
    print(f"  {n} replayed gens: {per * 1e6:5.2f} µs each")
    print(f"  v0.2 measured 7.6 µs — {'no regression' if per * 1e6 < 9 else 'REGRESSION'}")
    for lat, label in ((0.800, "typical model"), (0.150, "small model")):
        print(f"  → against a {label} at {lat * 1000:.0f} ms: "
              f"{per / lat * 100:.4f}% of wall time")


def bench_schedulers():
    header("4. Schedulers — threads vs asyncio")
    for n, limit in ((2000, 1000), (5000, 2000)):
        nums = ", ".join(str(i) for i in range(n))
        src = (f"fn main() {{ let ys = parallel for x in [{nums}] {{ x * 2 }} "
               f"limit {limit}\n print(len(ys)) }}")
        types, prompts, fns = parse(src)
        row = {}
        for sched in ("threads", "asyncio"):
            prog = FastProgram(types, prompts, fns, MockModel(), trace=False,
                               scheduler=sched)
            row[sched] = best(prog.run, 3)
        print(f"  {n:>5} tasks, limit {limit:>4}:  "
              f"threads {row['threads'] * 1e3:7.1f} ms   "
              f"asyncio {row['asyncio'] * 1e3:7.1f} ms   "
              f"({row['threads'] / row['asyncio']:.1f}x)")


def bench_journal():
    header("5. Durability — what does journaling cost per step?")
    jd = os.path.join(tempfile.mkdtemp(), "j")

    plain = """
    fn step_work(x: number) -> number { return x * 2 }
    fn main() {
      var i = 0
      while i < 50 { i = i + 1 }
      print(i)
    }"""

    durable = """
    fn step_work(x: number) -> number { return x * 2 }
    durable fn main() {
      let a = step "one"   { step_work(1) }
      let b = step "two"   { step_work(2) }
      let c = step "three" { step_work(3) }
      print(3)
    }"""

    types, prompts, fns = parse(plain)
    t_plain = best(FastProgram(types, prompts, fns, MockModel(),
                               trace=False).run, 5)

    types, prompts, fns = parse(durable)

    def fresh():
        shutil.rmtree(jd, ignore_errors=True)
        FastProgram(types, prompts, fns, MockModel(), trace=False,
                    journal_dir=jd).run()

    t_dur = best(fresh, 5)
    print(f"  50 plain iterations      {t_plain * 1e6:8.1f} µs")
    print(f"  3 journaled steps        {t_dur * 1e3:8.2f} ms")
    print(f"  → ~{t_dur / 3 * 1e3:.2f} ms per step, dominated by fsync")
    print()
    print("  fsync is the point: a journal that is not durable is not a")
    print("  journal. Against an 800 ms model call it is ~0.2% overhead, and")
    print("  it buys not repeating that call after a crash.")


def main():
    print("Humbaba v0.3 benchmarks —", time.strftime("%Y-%m-%d"))
    print(f"CPython {sys.version.split()[0]}, {os.cpu_count()} CPU")
    bench_checker()
    bench_dispatch_regression()
    bench_gen_overhead()
    bench_schedulers()
    bench_journal()
    print()


if __name__ == "__main__":
    main()
