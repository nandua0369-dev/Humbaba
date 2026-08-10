// ── Inbox Triage ─────────────────────────────────────────────────────────────
//
// Classifies a batch of emails in parallel, collects the urgent ones, then
// writes a brief summary for each.
//
// What this shows:
//   • parallel for with a real limit (16 at a time, not unbounded)
//   • gen<T> typed output — no JSON parsing, no hoping
//   • boolean logic and conditional branching
//   • a second gen<> stage that only runs on the emails that need it
//   • budget cap so a runaway batch can't spend without limit
//
// Run:
//   python3 humbaba.py run examples/sample_inbox.hb
//   python3 humbaba.py run examples/sample_inbox.hb --cassette /tmp/inbox.json
//   (second run is free — replayed from cassette)

type Triage {
  category: string     // "urgent" | "normal" | "spam"
  reason:   string
  score:    number     // 1-10 urgency
}

type Summary {
  headline: string
  action:   string
}

prompt triage(email: untrusted string) {
  system: "You are an inbox assistant. Classify the email.
Category must be exactly one of: urgent, normal, spam.
Score is urgency 1-10. Reason is one sentence."
  user: "Email: {email}"
}

prompt summarise(email: untrusted string) {
  system: "Summarise this urgent email in two fields:
headline: one line saying what it is about.
action: the single most important next step for the reader."
  user: "Email: {email}"
}

fn label(score: number) -> string {
  if score >= 8 { return "🔴 URGENT" }
  if score >= 5 { return "🟡 normal" }
  return "⚪ low"
}

fn main() uses { model } budget { max: 2.00 } {

  let emails = [
    "Subject: PRODUCTION DOWN — payment service returning 500
     Hi, our checkout is completely broken, customers can't pay,
     we are losing roughly £3000 per minute. Please help immediately.",

    "Subject: Lunch tomorrow?
     Hey, are you free for lunch tomorrow around 1pm?
     There's a new place that opened near the office.",

    "Subject: Security alert — unusual login detected
     We detected a login to your account from an unrecognised device
     in Lagos, Nigeria at 03:14 UTC. If this was not you, reset your
     password immediately.",

    "Subject: Q3 report attached
     Please find the Q3 performance report attached as requested.
     Let me know if you need any clarifications.",

    "Subject: WINNER NOTIFICATION — claim your £500 gift card
     Congratulations! You have been selected to receive a £500
     gift card. Click here to claim within 24 hours.",

    "Subject: Team standup notes — 8 Aug
     Quick notes from today: Alice is unblocked on the auth PR,
     Bob is OOO Wednesday, sprint review moved to Thursday 3pm."
  ]

  print("── Triaging", len(emails), "emails ──────────────────────────")

  // Classify all emails in parallel, 4 at a time
  let results = parallel for email in emails {
    gen<Triage> from triage(email: email)
  } limit 4

  // Show the triage results
  var i = 0
  var urgent_count = 0
  while i < len(results) {
    let t = results[i]
    print(label(t.score), "│", t.category, "│ score:", t.score, "│", t.reason)
    if t.category == "urgent" { urgent_count = urgent_count + 1 }
    i = i + 1
  }

  print("")
  print("── Urgent items:", urgent_count, "──────────────────────────")

  // Summarise only the urgent emails
  i = 0
  while i < len(results) {
    let t = results[i]
    if t.category == "urgent" {
      let s = gen<Summary> from summarise(email: emails[i])
      print("  HEADLINE:", s.headline)
      print("  ACTION:  ", s.action)
      print("")
    }
    i = i + 1
  }
}
