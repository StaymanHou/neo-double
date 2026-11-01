# The held-out set — the draft suite's evaluation material, withheld from the voice build

The draft suite scores drafts against messages the operator actually sent. The voice build writes
the voice model that produces those drafts. If the voice build reads the draft suite's messages, the
draft suite grades the model on its own input and **passes regardless of quality** — and nothing downstream would notice, because a
model reproducing text it has read looks exactly like a model that has learned a voice.

So the evaluation set is chosen and withheld *before* `self/style.md` exists. That ordering is
the whole reason this directory is here: carving the holdout is the first thing the voice build
does, not something the evaluation sorts out later.

The figures below are from one real carve, over a corpus of roughly 86,000 visible messages, and
are there to show the shape of the decisions. Yours will differ.

---

## The error is one-way

Holding out too many messages is recoverable: step 8 scores on a subset and the remainder sit
unused. Holding out too few is not. A message read into the voice model cannot be taken back
out, and the resulting pass is indistinguishable from a real one.

Every sizing decision below therefore leans toward over-holding. 60 messages is that lean, not
a minimum.

---

## What is in it

67 messages — 60 selected, plus 7 near-duplicates of them (below). The strata are what matter,
not the total.

| stratum | population | held out | spread across |
|---|---|---|---|
| `outlook-long` — Outlook ≥400 chars | 45 | 12 | 3 years |
| `telegram-en-long` — Telegram English ≥400 chars | 706 | 30 | 30 chats |
| `telegram-zh-long` — Telegram Mandarin ≥200 chars | 83 | 12 | 12 chats |
| `telegram-en-mid` — Telegram English 120–300 chars | 1,984 | 6 | 6 chats |
| `near-duplicate` — reproduces ≥50% of a selected message | 17,260 | 7 | — |

`MANIFEST.json` records a sha256, a date, a chat, a length and a script per row. **It records no
bodies.** Reading it tells you that a message exists, not what it says.

### Why each register gets its own threshold

The operator's framing for the draft suite was *"weighted deliberately to the long, context-dependent
messages — a two-line acknowledgement is matched by luck"*. Length was the proxy for
context-dependence, and the proxy does not survive contact with this corpus:

| register | n | median | p95 | ≥400 chars |
|---|---|---|---|---|
| Telegram English | 11,855 | 54 | 460 | 706 |
| Telegram Mandarin | 5,236 | **19** | 103 | **28** |
| Outlook English | 169 | 180 | 1,584 | 45 |

In this corpus the Mandarin register is short-form. A single 400-character threshold would have
produced a held-out set that is ~96% English and would never have tested the register used with a
whole group of correspondents — where a draft in the wrong language is wrong before it is read.
Taking each register's own long tail (200 chars is Mandarin's p99) costs 14% of that stratum, the
most expensive cut in the set. **Have the operator confirm which registers are in scope.**

### Why breadth beats proportionality

Selection is round-robin across chats — one message from each before a second from any. Thirty
messages to thirty correspondents tests register calibration thirty times; thirty messages to
one team room tests it once. Outlook spreads across *years* instead, because when `chat_key` is keyed by
sender, every outbound mail the operator has ever sent sits in one bucket under their own name.

### Reproducibility

Ordering is by sha256 of the row's own content, so the selection carries no intent — it cannot
be nudged toward messages that flatter the model. `carve.py` re-run on the same corpus produces
a byte-identical manifest; verified.

It will **not** reproduce on a larger corpus, because the strata are relative to the population.
`MANIFEST.json` is therefore the artefact of record and `carve.py --force` is a way to void
the draft suite, not a way to refresh it.

---

## Enforcement

A holdout that depends on remembering it is not a holdout — the same argument the fence itself
rests on. `harness/shim-voice/neo-corpus` goes first on PATH for any voice-build session:

```sh
export NEO_VOICE_HELDOUT=$PWD/harness/heldout/MANIFEST.json
export NEO_VOICE_SHIM_LOG=$PWD/harness/heldout/shim.log
export PATH=$PWD/harness/shim-voice:$PATH
```

It drops held-out rows from every verb that can emit a body — `outbound`, `thread`, `mail`,
`recent`, `search` — and refuses to serve those verbs as text at all. Filtering a
human-readable table by pattern-matching it is the kind of convention this codebase keeps
finding holes in; requiring `--json` makes the filter structural. `stats`, `chats`, `people`,
`roster` and `timeline` emit counts and names only and pass through untouched.

**It says when it withholds.** A row that vanishes silently is the defect this project keeps
rediscovering — a tool reporting absence when it means *withheld*. The count goes to stderr, so
a gap in a thread is something you can reason about rather than a silence you cannot.

### Three ways the filter would have been silently inert

Each of these makes a body hash to something other than itself. All three are handled, and all
three are pinned by `self-test.py`.

| | why it breaks the hash | what the shim does |
|---|---|---|
| `--preview N` | truncates the body | strips the flag, filters on the full body, re-applies the truncation byte-identically |
| `search` snippets | returns an **18-token snippet**, not the body | appends `--full`, so search is answered with real bodies |
| `search` markers | wraps matches in `«»` | strips them before hashing, leaves the output alone |

**The snippet one is the reason the self-test exists.** It was not caught by reading the code.
The shim looked correct, ran clean, printed no warning, and filtered *nothing* on the verb most
likely to be used during a voice build — while its own log recorded a successful run. A check
written to fail if the filter went inert caught it in the first execution.

Forcing `--full` costs the `«»` highlight markers, since those are produced by the snippet
function being replaced. Known trade-off, pinned as a check so it is not mistaken for a fault
mid-session. Use `--preview N` to shorten what comes back.

### Two ways held-out content reached the sample anyway

Both were found by the audit *after* the shim ran clean, which is the reason the audit goes
straight to the real binary instead of trusting the clamp.

**People broadcast.** The same 1,300-character message went to three colleagues on one day with
the greeting changed — 97.5% similar, three different hashes. One copy was held out; the other two sat in the training sample, so 99% of a withheld message was
readable under another correspondent's name. Byte-identical dedup cannot see this. `carve.py`
now measures containment across the whole outbound population and holds out the twins too:
**7 further messages**, taking the set from 60 to 67. Holding the twins rather than dropping the
test item is the over-hold direction, which is the safe one.

**Replies quote.** After exact-hash filtering ran clean, **44%** of one held-out Outlook message
was still recoverable from its own thread, because the replies beneath it quoted it back. A
quotation hashes to nothing in the manifest. The shim now also checks *containment* on the verbs
that can return somebody else's message — `thread`, `mail`, `recent`, `search` — using
`SHINGLES.json`, a one-way digest of every 32-character window of every held-out message. It
lets the shim recognise held-out content without ever holding a held-out body. `outbound` is the
operator's own prose only and keeps the cheap exact-hash path, which is what stops the 17,000-row query
paying for this.

### Why the window is 32 characters and not 8

This nearly went the other way, and the failure would have been invisible in the direction that
matters. The first audit matched any 160-character run and reported three leaks, of which **two
were the operator reusing a boilerplate paragraph**. Tightening it into a containment measure at k=8 then
reported *twenty-one*.

The calibration is a comparison whose true answer is known to be zero: an email message cannot
appear in a Telegram-only file. Measured across that boundary, containment reads **0.51 at k=8**
— which is to say, it is measuring English — and **0.03 at k=32**. Thresholds sit at 0.30 for a
leak and 0.15 for a note, an order of magnitude above the floor.

Two related traps, both hit and both worth not re-discovering:

- **`difflib.SequenceMatcher` is not usable here without `autojunk=False`.** Its heuristic
  treats characters appearing in more than 1% of a long sequence as junk, which on prose means
  most of the alphabet. It reported the two boilerplate cases as 3% and 1% overlap, and the one
  genuine 99% leak as 3%.
- **A check that fires on correct behaviour is worse than no check.** Both bad thresholds
  produced pages of red, and the only way through was to find a comparison that could not
  possibly be true and calibrate against it.

### Six duplicate bodies

The shim removes **66** rows for 60 held-out hashes: six of those messages were sent twice with
byte-identical text. Dropping every copy is correct — a twin left in the population *is* the
held-out message, readable under another date.

---

## What this does not guarantee

It hides a few dozen messages out of tens of thousands from the ordinary read path. It does not stop a session
reading `MANIFEST.json`, invoking the real binary by absolute path, or reaching the store
another way. Like the knowledge suite's cutoff shim, it is a clamp on the ordinary path.

The backstop is an audit, not trust: **every example quoted in `self/style.md` and
`self/values.md` gets checked against the held-out set, and a hit voids the build.** That check
is a measurement and belongs in closing the voice build (`harness/voice/audit.py`), not in a promise made here.

---

## Running it

```sh
python3 harness/heldout/carve.py --carved-on YYYY-MM-DD   # once; refuses if a manifest exists
python3 harness/shim-voice/self-test.py                   # before every voice-build session
```

The self-test reads bodies — it has to, to prove one was removed — and prints only hashes,
lengths and counts. 24 checks; all must pass before a session that writes the voice model
starts reading.
