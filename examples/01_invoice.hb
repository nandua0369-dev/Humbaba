// The spine of Humbaba: a type, a prompt, and a generation that must fit the type.

type Invoice {
  vendor: string
  total:  number
}

prompt extract(document: untrusted string) {
  system: "Extract the vendor and the total amount due."
  user:   "Document: {document}"
}

fn main() uses { model } budget { max: 0.50 } {
  let doc = "INVOICE from Acme Robotics. Ref 88. Amount due: 2400.00 GBP"

  policy { retry: 3, fallback: "small" } {
    let inv = gen<Invoice> from extract(document: doc)
    print("vendor:", inv.vendor)
    print("total: ", inv.total)
    print("vat:   ", inv.total * 0.2)
  }
}
