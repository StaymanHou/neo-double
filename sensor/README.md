# neo-sensor — the fenced layer

The only code between the agent and the operator's messages. It ingests Telegram and Outlook
traffic into a local SQLite corpus, **redacts secrets at write time**, emits only conversations the
operator has explicitly admitted, and owns the two gated send paths.

The agent never opens the database. It reads through `neo-corpus`, and the raw store, `sqlite3`,
and the disposition command are denied to it in the agent's permission settings (see the
framework's `settings.json`).

## The fence

| Layer | What it guarantees | Why it is code, not a convention |
|---|---|---|
| **Write-time redaction** | Every body is redacted on the way in. The readable column holds placeholders (`<CREDENTIAL_ASSIGNMENT>`, `<AWS_ACCESS_KEY_ID>`, `<BANK_DETAILS>`, `<ONE_TIME_CODE>`, …). The original goes to a separate raw column that no agent verb reads. | The predecessor gated egress on a `credentials_redacted` flag that every call site hard-coded `True`. It shipped real cloud keys to a model while reporting itself clean. Redacting before anything is readable means there is nothing to remember. |
| **Dispositions are consent** | Each conversation is `work`, `mixed`, `personal`, `ignore` or `undecided`. Only `work`/`mixed` bodies are emitted. An unruled conversation is **counted and named, not stored**. | Choosing to ingest a chat is not consent to show it to an agent. Admitting a chat exposes its bodies immediately and can't be undone, so `neo-channels set` is the operator's command, never the agent's. |
| **Metadata-only for pending** | `channels-pending` shows name, username, volume, dates and a redacted, attributed preview, enough for the operator to rule. It never shows bodies. | "Don't give me a bare ID and ask me to decide." A ruling needs context, and the fence decides how much. |
| **Deny-listed raw store** | `corpus.db`, `sqlite3`, `psql` and `docker` are denied to the agent. | Sessions often run in auto mode, where a command merely left off the allowlist is auto-approved. Only a real deny holds. |
| **Honest absence** | `search` falls back to a substring scan for CJK and kana, which a word tokenizer can't split. Bounded reads say on stderr what they dropped. A filter that empties a result names the filter instead of claiming the chat doesn't exist. | A tool that reports absence is making a claim. Each of these once said "nothing" when it meant something else. |

The filter's honest guarantee is **"the filter ran"**, not "no secret remains". Every recogniser
is measured over the whole corpus (`measure_recognisers`) before it ships. The comments in
`redaction.py` record each one's false-positive population and its known uncovered shapes.

## Commands

| Command | Verbs | Whose |
|---|---|---|
| `neo-corpus` | `stats` · `chats` · `people` · `outbound` · `thread` · `mail` · `roster` · `timeline` · `recent` · `search` · `channels-pending` | agent (read-only, fenced) |
| `neo-fetch` | `pull` (**dry run unless `--commit`**) · `enrich` · `reclassify` | agent |
| `neo-fetch` | `login` (Graph device code) · `telegram-login` | **operator**: an interactive secret |
| `neo-channels set <chat_key> <disposition>` | admit or rule a conversation | **operator**: it is the consent |
| `python -m neo_sensor.measure_recognisers` / `redact_corpus` | measure or re-run redaction over the raw column | **operator**: they read unredacted text |
| `neo-send` | Telegram post from message **files** | agent, gated (below) |
| `neo-mail` | `reply-all` to a corpus message from a `work` correspondent | agent, gated · `login` is the operator's |

Rule of thumb: a command is the operator's only when (1) running it *is* the consent, (2) it would
show raw, unredacted text, or (3) it needs an interactive secret. Everything else is the agent's
to run. Handing the operator a command that is the agent's own is a failure too.

## The send gate (`neo-send`, `neo-mail`)

- **The message is a file, never a string**, so "approved this, sent that" can't be constructed.
- **The footnote is a constant**: `— Drafted by <NEO_ASSISTANT_NAME>, my AI double. Reviewed,
  edited and approved by me.` It is appended, then **verified in the final bytes**. There's no
  per-send override.
- **Only channels ruled `work`.** The live chat title must still match the name the ruling was
  made against.
- **Approval:** `--approve --quote '<the operator's own words>'` writes `approval.json`, pinning
  a digest of each message's final bytes, the file list and its order, the target (and, for mail,
  the sorted recipient set), a 60-minute expiry and single use. `--commit` refuses without a live,
  matching approval, then appends to `sent.md` beside the draft.
- **Mail reads back** the reply-all draft Outlook created (recipients and rendered body) before
  sending, and deletes it on any mismatch. Its token is separate from the pull's, so the read path
  never holds a token that can send.
- The framework's `guard-send-approval` hook refuses an approval whose quote isn't verbatim in the
  operator's most recent message. The gate can't prove a human approved anything, because the
  agent runs both halves. What it does is make an unapproved send a *fabricated record*, in git.

## Configuration

| Variable | Meaning | Default |
|---|---|---|
| `NEO_STATE_DIR` | corpus, tokens and Telegram session | `~/.local/state/neo` |
| `NEO_ENV_FILE` | app credentials file | `~/.config/neo/.env` |
| `NEO_TELEGRAM_OPERATOR_ID` | the operator's numeric Telegram user id (decides `inbound`/`outbound`) | **required** for Telegram |
| `NEO_OPERATOR_EMAILS` | the operator's addresses, comma-separated | **required** for mail |
| `NEO_ASSISTANT_NAME` | name in the send footnote | `Neo` |
| `TELEGRAM_API_ID`, `TELEGRAM_API_HASH` | your Telegram API app | env or env file |
| `MS_GRAPH_CLIENT_ID`, `MS_GRAPH_TENANT_ID` | your Microsoft Graph app registration | env or env file |

Keep the state dir outside any repo and at mode 0700. Tokens and the session file are written 0600.

## Setup

1. Python 3.12, then `uv sync` (or `pip install -e .` plus the dev group). The spaCy model
   `en_core_web_lg` is pulled by the lockfile and is large.
2. **Telegram:** create an API app at my.telegram.org, then put the id and hash in the env file. The
   operator runs `neo-fetch telegram-login` (phone and code).
3. **Outlook / Microsoft Graph:** register a public-client app in your Entra ID tenant with
   delegated `Mail.Read` (pull) and, only if you want sending, `Mail.ReadWrite` + `Mail.Send` (the
   separate send token). Enable device-code flow. The operator runs `neo-fetch login`, and
   `neo-mail login` for sending.
4. `neo-fetch pull --commit` creates the corpus on first run. New conversations arrive `undecided`.
   The operator rules them with `neo-channels set`.
5. Point the agent's permission settings at the wrappers: allow `neo-corpus`, deny the raw store,
   `sqlite3` and `neo-channels`.

## Tests

`pytest` runs 185 tests. 23 of them assert the fence against a real corpus at
`$NEO_STATE_DIR/corpus.db` and skip when there is none. Run the suite after every pull: new rows
are checked against "no credential shape survives in any redacted body".
