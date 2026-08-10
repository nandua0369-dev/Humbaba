// ── Research Assistant ───────────────────────────────────────────────────────
//
// Given a list of topics, researches each in parallel, scores their
// relevance, filters for the most important, then synthesises a briefing.
//
// What this shows:
//   • multi-stage pipeline with data flowing between gen<> calls
//   • parallel fan-out followed by sequential processing
//   • while loop with accumulator pattern
//   • budget cap preventing runaway spend
//   • the type system catching a wrong field at compile time
//     (try changing `note.score` to `note.rating` — it won't compile)
//
// Run:
//   python3 humbaba.py run examples/sample_research.hb --cassette /tmp/research.json
//   python3 humbaba.py run examples/sample_research.hb --cassette /tmp/research.json
//   (second run is instant and free)

type Note {
  summary:  string
  score:    number    // relevance 1-10
  category: string    // "high" | "medium" | "low"
}

type Briefing {
  text: string
}

prompt research(topic: untrusted string) {
  system: "Research this topic. Write a two-sentence summary.
Score relevance to a software engineering team 1-10.
Category: high (>=7), medium (4-6), low (<=3)."
  user: "Topic: {topic}"
}

prompt brief(notes: untrusted string) {
  system: "You are a research assistant. Synthesise these research notes
into a concise briefing for a software engineering team. Two paragraphs max."
  user: "Notes to synthesise: {notes}"
}

fn label(score: number) -> string {
  if score >= 7 { return "🟢" }
  if score >= 4 { return "🟡" }
  return "🔴"
}

fn main() uses { model } budget { max: 3.00 } {

  let topics = [
    "prompt injection attacks in LLM-based agents",
    "capability-based security systems for AI",
    "structured concurrency in modern runtimes",
    "zero-knowledge proofs for AI model attestation",
    "CRDT-based conflict resolution for distributed state",
    "WebAssembly as a sandboxing target for untrusted code",
    "cost management strategies for LLM API usage"
  ]

  print("Researching", len(topics), "topics in parallel (limit 4)")
  print("")

  // Fan out: research all topics simultaneously, at most 4 at a time
  let notes = parallel for topic in topics {
    gen<Note> from research(topic: topic)
  } limit 4

  // Show results and collect high-priority notes
  print("── Research results ──────────────────────────────────")
  var i = 0
  var high_count = 0
  var combined = ""

  while i < len(notes) {
    let note = notes[i]
    print(label(note.score), "score:", note.score, "│", note.summary)
    if note.score >= 7 {
      high_count = high_count + 1
      combined = combined + topics[i] + ": " + note.summary + "  "
    }
    i = i + 1
  }

  print("")
  print("── High-relevance topics:", high_count, "──────────────────────")

  if high_count == 0 {
    print("No high-relevance topics found.")
    return 0
  }

  // Synthesise a briefing from the high-relevance notes only
  let briefing = gen<Briefing> from brief(notes: combined)
  print("")
  print("── Synthesised Briefing ──────────────────────────────")
  print(briefing.text)
}
