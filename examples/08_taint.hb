// Taint propagation: a value derived from untrusted input stays untrusted,
// and the compiler refuses to let it reach a capability.
//
// Uncomment the marked line to see the error:
//
//   line N: tainted value passed to 'db.dump'. It derives from `untrusted`
//   input; launder it explicitly first.

type Invoice { vendor: string  total: number }

prompt extract(document: untrusted string) {
  system: "Extract the vendor and the total amount due."
  user:   "Document: {document}"
}

fn main() uses { model, db.dump } {
  let inv = gen<Invoice> from extract(
    document: "INVOICE from Acme Robotics. Amount due: 2400.00"
  )

  // Reading and printing tainted data is fine.
  print("vendor:", inv.vendor, "total:", inv.total)

  // This is not. Uncomment to see the compiler stop it:
  // let leaked = db.dump(inv.vendor)

  // A literal was never untrusted, so this is allowed.
  let ok = db.dump("scheduled nightly export")
  print("dump ok")
}
