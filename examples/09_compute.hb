// ── Pure compute ─────────────────────────────────────────────────────────────
//
// Everything here is inside the IR subset: functions, arithmetic, comparison,
// branching, recursion, list literals, `for` comprehensions, indexing, len and
// print. No gen, no capabilities, no policy, and no mutation — `var`, `while`
// and assignment stay in the host runtime, so they are deliberately absent.
//
// Because it is in the subset, this program compiles to portable bytecode and
// runs on any host. It is the file to use when checking that a host agrees
// with the front end.
//
// Python:
//   python3 humbaba.py run   examples/09_compute.hb
//
// Bytecode, for the C VM and the Go runtime:
//   python3 humbaba.py build examples/09_compute.hb -o /tmp/compute.hbir
//   native/humbabavm   /tmp/compute.hbir
//   go/humbaba-runtime /tmp/compute.hbir
//
// All three must print exactly the same thing.

fn classify(n: number) -> number {
  if n > 100 { return 3 }
  if n > 10  { return 2 }
  return 1
}

fn fib(n: number) -> number {
  if n < 2 { return n }
  return fib(n - 1) + fib(n - 2)
}

fn sum_to(n: number) -> number {
  if n <= 0 { return 0 }
  return n + sum_to(n - 1)
}

fn main() {
  // arithmetic and precedence
  print(2 + 3 * 4 - 6 / 2)

  // recursion, twice over
  print(fib(18))
  print(sum_to(100))

  // branching across a list, results kept in order
  let xs = [4, 42, 400, 7, 250]
  let tags = for x in xs { classify(x) }
  print(len(tags))
  for t in tags { print(t) }
}
