# Humbaba — Language Reference (v0.1)

Complete. If a construct is not here, it does not exist yet.

---

## 1. Lexical structure

**Comments** run from `//` to end of line. There is no block comment.

**Identifiers** match `[A-Za-z_][A-Za-z0-9_]*`.

**Numbers** are `123` or `123.45`. Integers and floats are one type at runtime
(`number`); integer-valued results print without a decimal point.

**Strings** are double-quoted, with escapes `\n`, `\t`, `\"`, `\\`. There is no
single-quote form and no multi-line string.

**Whitespace and newlines are insignificant.** Statements are not
newline-terminated and there are no semicolons; the grammar is unambiguous
without them.

**Reserved words**

```
type prompt fn let return if else uses budget gen from
for parallel in limit policy untrusted true false
system user retry fallback max
```

`system`, `user`, `retry`, `fallback` and `max` are reserved only because the
lexer classifies them eagerly; they are accepted as field labels where the
grammar expects them.

---

## 2. Types

Four built-in types:

| Type | Values |
|---|---|
| `string` | text |
| `number` | integer or float, one runtime representation |
| `bool` | `true`, `false` |
| *list* | written as a literal `[a, b, c]`; not yet nameable in a declaration |

Plus user-declared record types:

```
type Invoice {
  vendor: string
  total:  number
  paid:   bool
}
```

Fields are separated by whitespace or an optional comma. Record types may only
contain the four built-ins — **nested record types are not yet supported**, and
neither are optional fields, defaults, or lists inside a record.

Records are constructed only by `gen<T>`. There is no literal constructor
syntax. This is deliberate for v0.1: the type exists to constrain a model, and
adding a second construction path invites the two to drift.

Field access is `value.field`. Reading an undeclared field is a runtime error
naming the type and the field.

---

## 3. Prompts

A prompt is a declaration, not a string. It has a name, typed parameters, and
two sections.

```
prompt extract(document: untrusted string, hint: string) {
  system: "Extract the vendor and the total amount due. {hint}"
  user:   "Document: {document}"
}
```

- Parameters are interpolated by name using `{param}` in either section.
- A parameter marked `untrusted` is **fenced** before the model sees it (§7.2).
- Both sections are optional; a missing one is the empty string.
- Interpolation is textual and applies only inside prompt bodies. Ordinary
  string literals elsewhere in a program are not interpolated.

Prompts are declarations so they can be diffed, reviewed and tested as code
rather than hidden inside expressions.

---

## 4. Functions

```
fn research(topic: string) -> Report
  uses { model, web.search }
  budget { max: 0.50 }
{
  ...
}
```

- The return type is parsed but **not currently checked**. It is documentation.
- `uses { … }` declares the capability set (§7.1). Absent means the empty set.
- `budget { max: N }` declares a spending ceiling (§7.3). Absent means the
  function shares its caller's budget.
- Both clauses are optional and may appear in either order.
- Parameters are positional at the call site. Named arguments are supported in
  `gen` calls only.
- Execution begins at `main` unless `--entry` says otherwise.

Recursion is permitted but there is no depth guard; deep recursion will exhaust
the host stack.

---

## 5. Statements

### `let`

```
let x = expr
```

Binds a name in the current block scope. **There is no assignment** — a name
cannot be rebound once bound in a scope. Inner blocks may shadow outer names.

### `return`

```
return expr
```

Unwinds to the enclosing function. A function that falls off the end returns
nothing, and using that value is an error.

### `if` / `else`

```
if inv.total > 1000 {
  print("large")
} else {
  print("small")
}
```

The condition is any expression; truthiness follows the host's rules (empty
string, empty list, `0` and `false` are false).

### `policy`

```
policy { retry: 3, fallback: "small" } {
  ...
}
```

Establishes failure-handling for every `gen` lexically inside the block (§7.4).
Policies nest; the innermost enclosing policy wins. A `gen` with no enclosing
policy gets `retry: 0` and no fallback.

### Expression statements

Any expression may stand alone as a statement. Its value is discarded, except
as the last statement of a loop body (§6.4, §6.5).

---

## 6. Expressions

### 6.1 Operators

| Precedence | Operators |
|---|---|
| highest | `.` field access, `(…)` call |
| | `*` `/` `%` |
| | `+` `-` |
| lowest | `==` `!=` `<` `>` `<=` `>=` |

All binary operators are left-associative. There are no boolean `and`/`or`/`not`
operators yet, no unary minus (write `0 - x`), and no ternary.

`+` concatenates strings and adds numbers. Mixing them is a runtime error.

### 6.2 Literals

```
42          3.14        "text"       true       false
[1, 2, 3]   ["a", "b"]  []
```

Lists are heterogeneous at runtime and unchecked.

### 6.3 `gen<T>`

```
let inv = gen<Invoice> from extract(document: doc, hint: "GBP only")
```

Arguments may be named (`document:`) or positional. Missing arguments are an
error naming the prompt and the missing parameters.

Requires the `model` capability. Semantics in §7.5.

### 6.4 `for` — sequential loop

```
let doubled = for x in xs {
  x * 2
}
```

An **expression**, not a statement. It evaluates the body once per item and
collects the value of each body's final expression statement into a list. Use
it as a bare statement to discard the results.

The loop variable is bound in a fresh scope per loop, not per iteration.

### 6.5 `parallel for` — concurrent loop

```
let summaries = parallel for doc in docs {
  gen<Summary> from summarize(text: doc)
} limit 20
```

Identical to `for` except that iterations run concurrently, at most `limit` at a
time (default 4). Results are returned **in submission order**, not completion
order. Semantics in §7.6.

---

## 7. Runtime semantics

### 7.1 Capabilities

Each function declares the set of privileged operations it may perform. The set
is enforced at runtime, and it can only shrink down the call stack:

> A function may be called only if every capability it declares is already held
> by its caller.

This is *attenuation*. It means a capability cannot be acquired by calling into
a library — only passed down or dropped. `main`'s declared set is the root
grant.

Capabilities currently recognised:

| Capability | Gates |
|---|---|
| `model` | every `gen<T>` |
| `web.search` | the `web.search(query)` builtin |
| `db.dump` | the `db.dump(reason)` builtin |

The set is closed in v0.1 — user-defined capabilities are not yet possible.

Violations name the offending function and what it holds:

```
handle_raw() attempted 'db.dump' but only holds ['model']
```

### 7.2 Untrusted parameters and fencing

When a prompt parameter is marked `untrusted`, its value is wrapped before
interpolation:

```
<<<HUMBABA-DATA:6a1f9c02>>>
…the untrusted text…
<<<END-HUMBABA-DATA:6a1f9c02>>>
```

and a sentence is appended to the system section telling the model that content
between those markers is third-party data and must never be treated as
instructions.

Two consequences worth being precise about:

1. **The author cannot forget.** Fencing is attached to the parameter
   declaration, not to the call site, so every call is covered.
2. **It is mitigation, not proof.** A sufficiently persuasive payload can still
   talk a model round. Fencing raises the cost of the attack; capabilities
   (§7.1) bound the damage when it succeeds. The two are meant to be worn
   together.

*Fixed in v0.2:* the nonce was originally derived from a content hash, so that
record/replay stayed deterministic — which made it predictable to whoever
supplied the content. Cassettes are now keyed on the *inputs* rather than the
rendered message, which removed that constraint, so the nonce is random and
forged fence openers in untrusted content are defanged before interpolation.

*Remaining weakness:* fencing is still text-level mitigation. It raises the cost
of an attack; it does not close the class. Capabilities (§7.1) are what bound
the damage when it succeeds.

### 7.3 Budgets

A budget is a ceiling in currency units attached to a function.

- **Sub-allocation is checked at call time.** A callee's declared budget must
  fit inside the caller's *remaining* allowance, or the call fails before doing
  any work.
- **Charges propagate upward.** Every charge is applied to the calling frame and
  every ancestor, so a child cannot spend money an ancestor has committed.
- **Failed generations are still charged.** Real providers bill for them.
- Exhaustion names the frame that ran out, its limit, its spend, and the size of
  the call that broke it.

A function with no `budget` clause shares its caller's frame. If no frame in the
chain declares a limit, spending is unbounded and merely reported.

### 7.4 Policies: hard and soft failure

Humbaba distinguishes two failure classes, because they call for different
responses:

| Class | Meaning | Sensible response |
|---|---|---|
| **hard** | the provider failed — timeout, 503, connection reset | retry the identical request |
| **soft** | a response arrived but is unusable — refusal, truncation, wrong shape | change something: model, prompt, or give up |

`policy { retry: N }` governs hard failures. `policy { fallback: "model" }`
governs soft ones: on the first soft failure the runtime switches model and
tries once more. If no fallback is configured, a soft failure consumes a retry
instead.

Conflating the two is why so much agent code retries uselessly in a loop.

### 7.5 `gen<T>` in full

1. Require the `model` capability.
2. Resolve the prompt; check every parameter is supplied.
3. Build the system and user messages, fencing untrusted parameters.
4. Derive the output schema from `T`.
5. Attempt, up to `retry + 1` times:
   - call the provider;
   - charge the budget chain (this happens whether or not the result is
     usable);
   - coerce the result to `T`, raising a soft failure on a missing field or a
     non-coercible value;
   - return the record.
6. On exhaustion, raise an error naming the type, the attempt count and the last
   cause.

Coercion is strict about presence and lenient about representation: a numeric
string will become a `number`, a missing field will not.

### 7.6 Structured concurrency

`parallel for` guarantees:

- **Bounded width** — never more than `limit` iterations in flight.
- **Ordered results** — index *i* of the result corresponds to index *i* of the
  input, regardless of completion order.
- **Nothing outlives the block** — if any iteration raises, the remaining
  futures are cancelled and the block does not return.

State shared across iterations is the budget chain, which is guarded by a lock.
Everything else is per-iteration scope. There is no synchronisation primitive
and no way to mutate an outer variable, which is what makes this safe.

*Known gap:* iterations race against a shared budget. Whichever charge arrives
after the limit is reached takes the error, so which iteration fails is
non-deterministic. See `docs/ROADMAP.md` §4.

### 7.7 Record and replay

With `--cassette FILE`, every provider response is keyed by a hash of
(model, system, user, schema) and stored. On a later run, a matching key is
replayed: no network, no latency, **no charge**.

This makes tests deterministic and free, and it makes the third run of a failing
pipeline as fast as the first was slow.

---

## 8. Errors

All runtime errors carry the function name and enough context to act on:

```
runtime error: budget exhausted in research(): limit 0.01, spent 0.0116,
this call needs 0.0064

handle_raw() cannot call dump_all(): it requires ['db.dump'] which
handle_raw() does not hold

gen<Invoice> failed after 4 attempt(s): large: could not satisfy the
requested shape
```

There is no exception handling. An error terminates the program. `policy` is the
only recovery mechanism, and it is deliberately narrow — see
`docs/ROADMAP.md` §5.

---

## 9. Command line

```
humbaba run FILE [options]

  --entry NAME        function to start at (default: main)
  --cassette PATH     record/replay provider responses
  --chaos P           probability each call fails transiently (default 0)
  --overloaded LIST   comma-separated models that return malformed output
  --seed N            RNG seed for chaos (default 7)
  --quiet             suppress the runtime trace and summary
  --backend B         `fast` (closure compiler, default) or `tree` (reference
                      walker). Both must produce identical output; a test
                      asserts it.
```

`--chaos` and `--overloaded` exist to make failure paths reachable in a demo or
a test without waiting for a real provider to have a bad day.

---

## 10. Added in v0.3

`var` and assignment · `while` / `break` / `continue` · `and` / `or` / `not` ·
unary minus · nested record types · list types `[T]` · optional fields `T?` ·
list indexing `xs[i]` · record literals `T { … }` · `try` · `durable` / `step` ·
`capability` declarations · `import` · static type checking · taint propagation

## 11. Still not in the language

no `while ... else` · no first-class functions · no generics · no maps or sets ·
no string interpolation outside prompts · no `for` over anything but a list ·
no user-defined operators

See [LIMITATIONS.md](LIMITATIONS.md) for what is unverified rather than absent.

**Language:** no assignment · no boolean operators · no unary minus · no `while` ·
no `break`/`continue` · no nested record types · no lists in records ·
no optional fields · no user-defined capabilities · no modules or imports ·
no string interpolation outside prompts · no return-type checking ·
no static type checking of any kind · no `durable`/`step` · no real providers ·
no compilation
