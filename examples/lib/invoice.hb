// A shared module. Imported by 06_modules.hb.
type Invoice { vendor: string  total: number }

prompt extract(document: untrusted string) {
  system: "Extract the vendor and the total amount due."
  user:   "Document: {document}"
}
