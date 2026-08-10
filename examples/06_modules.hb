// v0.3: imports, assignment, while, boolean operators, records, try.
import "lib/invoice.hb"

type Summary { count: number  flagged: bool }

fn tier(total: number) -> string {
  if total > 1000 and total < 100000 { return "standard" }
  if total >= 100000 or total == 0 { return "review" }
  return "small"
}

fn main() uses { model } budget { max: 1.00 } {
  let docs = [
    "INVOICE from Acme Robotics. Amount due: 2400.00",
    "INVOICE from Globex Ltd. Amount due: 120.00"
  ]

  var flagged = 0
  var i = 0
  while i < len(docs) {
    let inv = gen<Invoice> from extract(document: docs[i])
    print(inv.vendor, inv.total, tier(inv.total))
    if not (inv.total > 1000) { flagged = flagged + 1 }
    i = i + 1
  }

  let s = Summary { count: len(docs), flagged: flagged > 0 }
  print("processed:", s.count, "any flagged:", s.flagged)
}
