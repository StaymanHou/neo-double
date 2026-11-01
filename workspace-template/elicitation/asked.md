<!-- THE SAY-IT-ONCE LEDGER. Read this BEFORE asking the operator anything.

     A fresh session has no memory of what was already asked, so "you already told me" cannot
     be honoured from memory — it is honoured from this file. Append every question you ask
     and what came back.

     This file is the record of the ANSWER, not the channel for the QUESTION. He does not read
     it. Ask in your reply, in the turn you find the gap; then record the answer here.

     Format:
       ## Q-NNN — <the question>
       - **Status:** asked | answered | declined | stale
       - **Asked:** YYYY-MM-DD
       - **Answer:** <verbatim where the exact wording matters>
       - **Landed in:** <path to the knowledge file that now holds it>

     IDs are never reused. `declined` means he chose not to answer — do not re-ask.
     `stale` means the answer may have changed; only a detectable change (role change, org
     change) licenses re-asking, and re-asking resets the ID's status, not the ID. -->

# Elicitation ledger

## Q-001 — What does "ACX" stand for? (synthetic example)
- **Status:** answered
- **Asked:** YYYY-MM-DD
- **Context:** Used constantly in team chat, never expanded in anything read.
- **Answer:** *"Acme Customer Experience — the support team."*
- **Landed in:** `knowledge/orgs/acme-customer-experience.md`
