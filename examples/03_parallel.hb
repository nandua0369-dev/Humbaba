// Structured concurrency: 8 documents, 3 at a time, results in order.

type Summary {
  text: string
}

prompt summarize(doc: untrusted string) {
  system: "Summarise in one line."
  user:   "Text: {doc}"
}

fn main() uses { model } budget { max: 2.00 } {
  let docs = [
    "Quarterly report from Northwind Traders, revenue 120000",
    "Support ticket from Globex about login failures, priority 2",
    "Invoice from Initech, amount 4500",
    "Meeting notes from Umbrella Corp, 6 attendees",
    "Contract renewal from Stark Industries, term 24 months",
    "Expense claim from Wayne Enterprises, total 310",
    "Incident report from Cyberdyne Systems, severity 1",
    "Purchase order from Tyrell Corporation, quantity 90"
  ]

  let summaries = parallel for d in docs {
    gen<Summary> from summarize(doc: d)
  } limit 3

  print("got", len(summaries), "summaries, in submission order:")
  print(summaries)
}
