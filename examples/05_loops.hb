// Sequential `for` is an expression: it collects each iteration's value.
// Use it when order matters or when calls must not overlap (rate limits).

type Score { value: number }

prompt rate(item: untrusted string) {
  system: "Score this item out of 100."
  user:   "Item: {item}"
}

fn describe(s: Score) -> string {
  if s.value > 50 { return "high" }
  return "low"
}

fn main() uses { model } budget { max: 1.00 } {
  let items = ["widget 90", "gadget 20", "sprocket 70"]

  // sequential: one at a time, in order
  let scores = for it in items {
    gen<Score> from rate(item: it)
  }

  // ordinary iteration over the results
  for s in scores {
    print(s.value, describe(s))
  }

  // same work, three at once
  let fast = parallel for it in items {
    gen<Score> from rate(item: it)
  } limit 3

  print("parallel got", len(fast), "results in the same order")
}
