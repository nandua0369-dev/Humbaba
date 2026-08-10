// ── Document Pipeline ────────────────────────────────────────────────────────
//
// Processes a batch of invoices through three stages. The whole pipeline is
// durable — if it crashes between steps, it resumes from where it left off
// without re-spending on completed work.
//
// What this shows:
//   • durable fn with named steps
//   • chaining gen<> results: the output of one feeds the next
//   • conditional logic on typed fields
//   • while loop with counter
//   • parallel processing with a safety budget
//
// Run:
//   python3 humbaba.py run examples/sample_pipeline.hb --journal /tmp/pipeline_j
//
//   Kill it partway through (Ctrl-C), then run again with the same flag —
//   it will resume from the first incomplete step.

type Invoice {
  vendor: string
  total:  number
  valid:  bool
}

type Review {
  status:  string     // "approved" | "flagged" | "rejected"
  reason:  string
}

type Report {
  text: string
}

prompt parse_invoice(document: untrusted string) {
  system: "Extract the vendor name, total amount, and whether the invoice appears
valid (has a clear total, a recognisable vendor, and no obvious errors).
valid is false if the document looks suspicious or incomplete."
  user: "Invoice document: {document}"
}

prompt review_invoice(vendor: untrusted string, total: number) {
  system: "Review this invoice. Status must be exactly one of:
  approved  — routine invoice, looks fine
  flagged   — unusual amount or unfamiliar vendor, needs a human check
  rejected  — clear problem (duplicate, fraudulent, missing data)
Reason is one sentence."
  user: "Vendor: {vendor}  Total: £{total}"
}

prompt write_summary(vendor: untrusted string, status: string, total: number) {
  system: "Write a one-paragraph summary suitable for the finance team."
  user: "Vendor: {vendor}  Status: {status}  Total: £{total}"
}

fn flag(total: number) -> bool {
  return total > 5000 or total == 0
}

durable fn process(doc: string) uses { model } {
  let inv  = step "parse"  { gen<Invoice> from parse_invoice(document: doc) }
  let rev  = step "review" { gen<Review>  from review_invoice(vendor: inv.vendor, total: inv.total) }
  let rep  = step "report" { gen<Report>  from write_summary(vendor: inv.vendor, status: rev.status, total: inv.total) }
  return rep
}

fn main() uses { model } budget { max: 5.00 } {

  let documents = [
    "INVOICE #1042 from Acme Robotics Ltd. Services rendered Aug 2026. Total due: £2400.00",
    "INVOICE from GlobalEx Solutions. Consulting fees Q3. Amount: £18500.00",
    "Invoice — Petty cash reimbursement. Staff lunch. £47.50",
    "INV-9981 Renewal — CloudHost Pro subscription annual. Vendor: Nimbus Systems. £1200.00",
    "OVERDUE NOTICE: Original invoice #887 from FastFreight Logistics. £340.00 + £34 late fee."
  ]

  print("Processing", len(documents), "invoices through 3-stage pipeline")
  print("Each stage is journaled — safe to interrupt and resume")
  print("")

  var i = 0
  var approved = 0
  var flagged_count = 0

  while i < len(documents) {
    print("── Invoice", i + 1, "─────────────────────────────────────")

    let inv = gen<Invoice> from parse_invoice(document: documents[i])
    print("  Vendor:", inv.vendor)
    print("  Total:  £", inv.total)
    print("  Valid: ", inv.valid)

    if not inv.valid {
      print("  ⛔ Skipped — document does not appear to be a valid invoice")
      i = i + 1
      continue
    }

    let rev = gen<Review> from review_invoice(vendor: inv.vendor, total: inv.total)
    print("  Status:", rev.status, "—", rev.reason)

    if rev.status == "approved" {
      approved = approved + 1
      print("  ✅ Approved for payment")
    }
    if rev.status == "flagged" or flag(inv.total) {
      flagged_count = flagged_count + 1
      print("  ⚠️  Flagged for human review")
    }

    i = i + 1
    print("")
  }

  print("── Summary ──────────────────────────────────────────")
  print("  Total processed:", len(documents))
  print("  Approved:       ", approved)
  print("  Flagged:        ", flagged_count)
}
