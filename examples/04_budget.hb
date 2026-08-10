// Nested budgets. research() is allowed 0.05 out of main's 1.00.
// It spends it, and the runtime stops it rather than sending a bill.

type Report {
  text: string
}

prompt investigate(topic: untrusted string) {
  system: "Write a short report on the topic."
  user:   "Topic: {topic}. Consider background, evidence, and open questions."
}

fn research(topic: string) uses { model } budget { max: 0.012 } {
  let a = gen<Report> from investigate(topic: topic)
  print("  pass 1 ok")
  let b = gen<Report> from investigate(topic: topic + " (second pass, more depth)")
  print("  pass 2 ok")
  let c = gen<Report> from investigate(topic: topic + " (third pass, deeper still, with citations)")
  print("  pass 3 ok")
  return c
}

fn main() uses { model } budget { max: 1.00 } {
  print("researching...")
  let r = research("verifiable computation")
  print("done:", r.text)
}
