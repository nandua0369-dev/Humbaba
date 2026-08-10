// Crash recovery. Run it, kill it mid-way, run it again — it resumes.
//
//   python3 humbaba.py run examples/07_durable.hb --journal /tmp/hbj
//   (ctrl-C during step 2)
//   python3 humbaba.py run examples/07_durable.hb --journal /tmp/hbj
//
// Steps already completed are replayed from the journal, not re-executed,
// and the money already spent is not spent again.

type Report { text: string }

prompt write(topic: untrusted string) {
  system: "Write a short report on the topic."
  user:   "Topic: {topic}"
}

durable fn main() uses { model } budget { max: 1.00 } {
  let a = step "research"  { gen<Report> from write(topic: "tidal energy") }
  print("1. research done")

  let b = step "expand"    { gen<Report> from write(topic: "barn humbaba habitats") }
  print("2. expansion done")

  let c = step "finalise"  { gen<Report> from write(topic: "tidal energy, final") }
  print("3. finalised")

  return c
}
