# Contributing

Contributions are welcome. There is one legal step first, and it exists for a
specific reason.

## The CLA, and why it is here

Humbaba is dual licensed: AGPL-3.0 for open-source use, and a commercial
licence for organisations that cannot accept the AGPL's source-disclosure
obligations.

Offering a commercial licence requires holding all rights in the work. If a
contribution arrives under the AGPL alone, that patch cannot be included in
commercially licensed copies — and the two versions diverge permanently.

So contributions need a **Contributor Licence Agreement**: you retain copyright
in your work, and you grant the maintainer a licence broad enough to
sublicense it under both licences.

This is the same arrangement used by projects with the same model, including
Qt, MongoDB and GitLab. It is not a transfer of ownership.

If you would rather not sign one, that is entirely reasonable. Open an issue
describing the change instead — a well-written bug report is often more useful
than a patch.

## Before you write code

Open an issue first for anything beyond a small fix. Humbaba deliberately does
less than most languages, and `docs/LANGUAGE.md` lists things that are absent
on purpose rather than by omission. A patch that adds one of them will be
declined however good it is, and it is better to find that out first.

## Standards for a patch

- **Tests.** Every guarantee in this project is asserted by a test. A change
  to behaviour needs a test that fails before it and passes after.
- **Both backends.** If you touch the language, the tree-walking reference
  interpreter and the closure compiler must still agree byte-for-byte.
  `TestBackendEquivalence` checks this.
- **All three hosts.** If you touch the IR, the Python reference VM, the C VM
  and the Go runtime must still agree, and the opcode numbering test must pass.
- **No new dependencies.** Humbaba imports only the standard library. This is
  a hard constraint, not a preference.
- **Measure, don't estimate.** Performance claims go in `docs/PERFORMANCE.md`
  with the number and the machine it was measured on. Estimates in this project
  have been optimistic by 2–5× every time they were checked.

## Running the tests

```bash
python3 -m unittest tests/test_humbaba.py tests/test_v3.py tests/test_bound.py
HUMBABA_BACKEND=tree python3 -m unittest tests/test_humbaba.py
cd go && make check
```

All must pass.
