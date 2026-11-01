"""Pre-egress credentials-redaction pipeline.

Implements `arch.md` § Core invariants → Invariant 3 (pre-egress
credentials-redaction). The contract:

- Identity entities (``PERSON``, ``EMAIL_ADDRESS``, ``PHONE_NUMBER``,
  ``LOCATION``, ``NRP``, ``URL``, ``DATE_TIME``) pass through to Gemini
  unchanged — the personal-assistant use case requires the LLM to reason
  over who said what to whom and when.
- Credentials (``CREDIT_CARD``, ``IBAN_CODE``, ``US_BANK_NUMBER``,
  ``US_SSN``, ``US_ITIN``, ``US_PASSPORT``, ``US_DRIVER_LICENSE``,
  ``UK_NHS``, ``MEDICAL_LICENSE``, ``CRYPTO``, ``IP_ADDRESS``,
  ``MAC_ADDRESS``) plus custom recognizers (``JWT``, ``GITHUB_PAT``,
  ``URL_WITH_CREDENTIALS``, ``PASSWORD``, ``GENERIC_BEARER_TOKEN``,
  ``AWS_ACCESS_KEY_ID``, ``CLOUD_SECRET``, ``CREDENTIAL_ASSIGNMENT``,
  ``CREDENTIAL_PAIR``, ``US_SSN`` backstop) are redacted to
  ``<ENTITY_TYPE>`` placeholders.

Two shapes of stated credential, deliberately separate recognizers:

- ``CREDENTIAL_ASSIGNMENT`` — ``label: value`` / ``label=value``, anchored
  on the label word.
- ``CREDENTIAL_PAIR`` — ``account/secret`` with **no label at all**,
  anchored on the account identifier. Added 2026-08-14 after a live
  password reached the agent because nothing in the module could match a
  login handed over the way a person actually hands one over.

Two recognizer postures, deliberately different (see SURFACE-2026-07-25):

- **Context-mandatory** (``PASSWORD``, ``GENERIC_BEARER_TOKEN``) — a
  generic high-entropy shape is only a credential if a context word says
  so, else prose and ids over-redact. Base score 0.3 + boost, per below.
- **Context-free** (``AWS_ACCESS_KEY_ID``, ``CLOUD_SECRET``, ``JWT``,
  ``GITHUB_PAT``) — the match is self-identifying (fixed vendor prefix,
  or the secret's own key name captured inside the match), so it fires at
  0.9 outright. This posture exists because the context-mandatory path
  structurally cannot catch secrets in machine-generated JSON: Presidio's
  context window is word-adjacency based, and ``"secretAccessKey":"…"``
  welds key to value in one punctuation run, so no boost ever lands. A
  real AWS key pair reached an assembled inference prompt that way —
  detected at 0.30, silently dropped by ``SCORE_THRESHOLD``.

Egress-only: this module is invoked by ``core/router.py`` immediately
before each ``LLMRequest`` dispatch. It is NOT called by ingestion;
the observation log stores raw text. Anyone can import this module
(the boundary is documented, not enforced at the import level —
only ``core/router.py`` checks the ``credentials_redacted`` flag).

Design source: the predecessor system's Presidio probe notes (not included in this repo).

Context-mandatory recognizers: ``PASSWORD`` and ``GENERIC_BEARER_TOKEN``
require both a pattern match AND a nearby context word per Decision 4.
Presidio does not natively make context mandatory — only score-boosting.
Enforced here by setting the base pattern score to 0.3 (below
``SCORE_THRESHOLD = 0.5``); the +0.35 context boost lifts a contextual
match to 0.65, while a no-context match stays at 0.3 and is silently
filtered.

Overlap resolution: when multiple recognizers fire on overlapping spans,
``_resolve_overlaps`` picks the higher-score detection and, on score
ties, the more-specific entity (custom recognizers beat default Presidio
entities). See ``_CUSTOM_ENTITY_PRIORITY``.

Known limitations inherited from the predecessor's Presidio probe:

- ``US_SSN`` vanilla recognizer does not fire on canonical-shape SSNs;
  closed in WP13 via the ``US_SSN`` custom backstop recognizer.
- ``US_DRIVER_LICENSE`` low-confidence FP on ``"Q3"`` (score 0.3);
  closed in WP13 via score-threshold filtering at 0.5.
- 0.85-score FPs on ``PERSON`` / ``LOCATION`` / ``NRP`` / ``URL`` are
  benign because those entities pass through under Decision 1.
- ``ORGANIZATION`` is not in Presidio 2.2 English supported_entities;
  out of scope for WP13 (credentials-only posture).
- Non-Western single-name ``PERSON`` NER is weak; moot because
  ``PERSON`` passes through under Decision 1.
"""

from __future__ import annotations

import re
from typing import Any

from presidio_analyzer import AnalyzerEngine, Pattern, PatternRecognizer
from presidio_anonymizer import AnonymizerEngine
from pydantic import BaseModel

SCORE_THRESHOLD = 0.5

# --- Entity policy: an ALLOW-list, not an identity deny-list ------------------------------
#
# The original design was a deny-list — redact everything Presidio reports except a named set
# of identity entities. That is the wrong shape once redaction runs **at rest**, because the
# redacted body becomes the agent's only view of the corpus, so a false positive is permanent
# corpus damage rather than one degraded prompt. A deny-list also silently admits every
# recogniser we never considered: measured over 3,000 real rows, `UK_NHS` fired on an Amazon
# ASIN (`/dp/0062362801/`) and `IP_ADDRESS` fired 23 times on IT-support conversations, which
# are a core FAQ use case and contain no credential at all.
#
# So: name exactly what gets removed. Anything not listed passes through byte-for-byte.
# Identity deliberately passes through — per the standing routing decision, PERSON / EMAIL /
# PHONE / LOCATION / NRP / URL / DATE_TIME reach the model, because translate and
# per-recipient calibration cannot work without knowing who is being written to.
REDACT_ENTITIES: frozenset[str] = frozenset(
    {
        "ANTHROPIC_API_KEY",
        "AWS_ACCESS_KEY_ID",
        # The one entry that is not a credential. His ruling on Q-042, 2026-08-17: a labelled bank
        # wire block authorises payment, and a third party who reads it can act with it, so it comes
        # out. Scope is credentials-plus-payment-instructions from here, and the line is drawn at a
        # 9-digit routing number beside a named account — see _BANK_ROUTING_REGEX for why it has to
        # be that narrow and what happens if it is not.
        "BANK_DETAILS",
        "CLOUD_SECRET",
        "CONSOLE_LOGIN",
        "CREDENTIAL_ASSIGNMENT",
        "CREDENTIAL_PAIR",
        "GITHUB_PAT",
        "JWT",
        "NETWORK_CREDENTIAL",
        # New 2026-08-31. Third unredacted instance in twelve days, and live ingest means a code
        # inside its expiry window is now reachable. 70/70 measured precision; see the regex comment.
        "ONE_TIME_CODE",
        "STANDALONE_SECRET",
        "URL_WITH_CREDENTIALS",
        "US_SSN",
    }
)
# CREDIT_CARD was trialled and REMOVED. Measured over the full 86k-row corpus it fired 80
# times with a 100% false-positive rate: Luhn happens to validate Meta ad-account numbers and
# Facebook page ids, so it masked `Acme News Ad Account (123456789012347)` on 79
# visible rows — an ad-revenue fact the operator works with daily — and found no card at all.
# The posture here is credentials-only; a 15-digit number in a work comms corpus is an account
# identifier, not a payment instrument. If a real card ever lands, nothing catches it: that is
# the same acknowledged limit as every other unrecognised shape.

# Retained for the module's public surface; identity is now implied by absence from
# REDACT_ENTITIES rather than by an explicit exclusion.
IDENTITY_ENTITIES: frozenset[str] = frozenset(
    {"PERSON", "EMAIL_ADDRESS", "PHONE_NUMBER", "LOCATION", "NRP", "URL", "DATE_TIME"}
)

# Custom-recognizer entity types, ranked in order of "specificity" for
# overlap resolution. When two analyzer results cover the same span, the
# entity earlier in this tuple wins; both beat default Presidio entities.
_CUSTOM_ENTITY_PRIORITY: tuple[str, ...] = (
    "ANTHROPIC_API_KEY",
    "AWS_ACCESS_KEY_ID",
    "CLOUD_SECRET",
    # Above NETWORK_CREDENTIAL and both credential shapes, on the same widest-span-wins reasoning
    # as the note below: its match runs from the sign-in URL through the secret, so it strictly
    # contains anything CREDENTIAL_PAIR or CREDENTIAL_ASSIGNMENT could match inside it.
    "CONSOLE_LOGIN",
    # NETWORK_CREDENTIAL outranks CREDENTIAL_ASSIGNMENT deliberately: when both fire on the same
    # text its span is the wider one (label + name + secret), and on a split the half that gets
    # published is the secret. Widest-span-wins is the safe tie-break here.
    "NETWORK_CREDENTIAL",
    "CREDENTIAL_ASSIGNMENT",
    "CREDENTIAL_PAIR",
    # Below the credential shapes and above the generic ones. Its spans are label→number and
    # collide with almost nothing; the one plausible overlap is a 9-digit routing number that some
    # other recogniser also reaches, and in that case the more specific credential entity should
    # win the label.
    "BANK_DETAILS",
    "GITHUB_PAT",
    "JWT",
    # Below every credential shape on purpose. Its span is label→digits and overlaps little, but
    # where it DOES overlap — a console login whose password happens to be numeric, a CJK password
    # label near the word "code" — the more specific credential entity should keep the label, and
    # its span is the wider one.
    "ONE_TIME_CODE",
    "URL_WITH_CREDENTIALS",
    "US_SSN",
    # Last on purpose. It matches the whole body, so it overlaps everything; ranking it lowest
    # means any recogniser that can name the secret more precisely keeps the label, and this one
    # only decides the outcome when nothing else fired.
    "STANDALONE_SECRET",
)

# Inherited verbatim from WP10 Phase 2 (``scripts/probes/presidio_smoke.py``).
# (From the predecessor's Presidio probe notes, not included.)
_JWT_REGEX = r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"
_GITHUB_PAT_CLASSIC_REGEX = r"\bghp_[A-Za-z0-9]{36,}\b"
_GITHUB_PAT_FINE_REGEX = r"\bgithub_pat_[A-Za-z0-9_]{82,}\b"

# WP13-new regexes per presidio.md § 8 Design items 5-6.
_URL_WITH_CREDENTIALS_REGEX = r"\bhttps?://[^\s]+[?&](?:token|api_key|password|secret)=[^\s&#]+\S*"
# Self-identifying cloud access-key ids: a fixed vendor prefix + fixed length.
# Unlike GENERIC_BEARER_TOKEN these need NO context word — the prefix alone is
# unambiguous, and a bare key in prose leaks just as badly as one in a config
# blob (SURFACE-2026-07-25). AKIA = long-term AWS key, ASIA = temporary/STS.
_AWS_ACCESS_KEY_ID_REGEX = r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"
# Anthropic API keys. Same posture and the same reasoning as the AWS prefix above: the vendor
# prefix is self-identifying, so it fires context-free and cannot over-redact.
#
# Added 2026-08-16, after eight live-shaped keys came back in full from a single
# `neo-corpus search`. The module had **no Anthropic pattern at all** — the whole recogniser
# set was written before this organisation started handing Claude Code keys to staff over
# Telegram, and nothing re-examined it when they did. The operator confirms these keys were
# already disabled, so this is a filter finding rather than an incident.
#
# `-` and `_` are both in the body class: real keys contain both. The `{40,}` floor is well
# under the observed ~95-char length and well over anything that could collide with prose.
_ANTHROPIC_API_KEY_REGEX = r"\bsk-ant-[A-Za-z0-9_-]{40,}"
# Secret VALUES named by a self-identifying key, matched as a key→value pair.
# Deliberately NOT left to GENERIC_BEARER_TOKEN's context-boost path: Presidio's
# context window is word-adjacency based, and in machine-generated JSON
# (``"secretAccessKey":"6+Wl…"``) the key and value are welded into one dense
# punctuation run, so the key name is never tokenized as a neighbouring word and
# the +0.35 boost never lands. That is the exact mechanism behind the live leak
# in SURFACE-2026-07-25 — the value WAS detected at 0.30 and silently dropped.
_CLOUD_SECRET_VALUE_REGEX = (
    r"(?i)(?:secret_?access_?key|secret_?key|client_?secret|private_?key|"  # noqa: S105 — regex
    r"session_?token|api_?secret|access_?token|refresh_?token)"
    r"[\"']?\s*[:=]\s*[\"']?[A-Za-z0-9+/_\-.=]{16,}"
)
_US_SSN_BACKSTOP_REGEX = r"\b\d{3}-\d{2}-\d{4}\b"
# Credential stated as `LABEL: value` / `LABEL=value`. Context-FREE: the label is the
# identifying signal and it is INSIDE the match, so no word-adjacency boost is involved.
#
# The span deliberately covers label *and* value, which is what makes it safe. The recogniser
# this replaces (`PASSWORD`, a bare word-shape lifted by a nearby context word) matched the
# label and the value as two separate candidates, and overlap resolution kept whichever sorted
# first — so `| Password: zqx2026` was rewritten to `| <PASSWORD>: zqx2026`, **masking the
# label and publishing the secret**. Measured on real rows, the same recogniser also rewrote
# `reset pw in my house earlier` -> `... my house <PASSWORD>` and `Amazon affilate center` ->
# `Amazon <PASSWORD> center`. One span over both halves cannot fail that way.
_CREDENTIAL_ASSIGNMENT_REGEX = (
    r"(?i)\b(?:password|passcode|passphrase|passwd|pwd|pw|pin|otp|one[- ]?time[- ]?code|"  # noqa: S105
    r"secret|token|api[_ -]?key|auth[_ -]?key|access[_ -]?code)"
    r"[\"']?\s*[:=]\s*[\"']?[^\s\"',;]{3,}"
)
# Token-shape after a bearer/auth header. Captures the value (token); the
# context word boost is what makes this fire.
# Credential stated as `<account> <sep> <secret>` with NO label word anywhere — the shape a
# person uses when handing someone a login in chat. Added 2026-08-14 after a live bit.ly
# password reached the agent verbatim through `neo-corpus`:
#
#     Bit.ly alice@example.com/ Bluebird777!!!
#
# Every existing recogniser structurally could not fire on it. CREDENTIAL_ASSIGNMENT needs a
# label word (`password`, `token`, …) followed by `:` or `=`; here the "label" is an email
# address and the separator is a slash. CLOUD_SECRET needs a self-identifying key name.
# URL_WITH_CREDENTIALS needs a URL. The gap was never a scoring failure — nothing matched at
# all, so no threshold or context tuning would have helped.
#
# Context-FREE, same posture as CREDENTIAL_ASSIGNMENT and for the same reason: the identifying
# signal (an account identifier) is INSIDE the match, and the span covers account *and* secret
# so overlap resolution cannot mask one half and publish the other.
#
# The value shape is deliberately tight, because redaction runs at rest and a false positive is
# permanent corpus damage rather than one degraded prompt:
#   - ≥ 6 chars, and must contain BOTH a letter and a digit-or-password-special. `…/ 2026`
#     (no letter) and `…| Media Trends` (no digit) are left alone.
#   - `/`, `?` and `=` are excluded from the value, which is what stops URL tails such as
#     `noreply@x.com/unsubscribe?id=123abc` from matching.
#   - A preceding `:` or `/` disqualifies the account half, so URL userinfo
#     (`https://user@host/path1`) and `mailto:` links never reach the value test.
_CREDENTIAL_PAIR_REGEX = (
    r"(?<![:/\w])"
    r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"
    r"\s*[/|]\s*"
    r"(?=[^\s/?=]{6,})"
    r"(?=[^\s/?=]*[A-Za-z])"
    r"(?=[^\s/?=]*[\d!#$%^&*()_+\[\]{};'\"\\,<>])"
    r"[^\s/?=]+"
)
# Credential stated as `<network label>: <network name> / <secret>` — added 2026-08-15 after a
# live guest-wifi password reached the agent through `neo-corpus`:
#
#     Guest Wifi: CP Guest / iBbAX~mG8-a+
#
# Why every existing recogniser missed it, which is the useful part:
#   - CREDENTIAL_ASSIGNMENT anchors on a label word from a fixed list (`password`, `token`, …)
#     directly followed by `:` or `=`. The label here is a *network* word, which is not on that
#     list, and the thing after the colon is the SSID rather than the secret.
#   - CREDENTIAL_PAIR requires an **email address** left of the separator. `CP Guest` is not one.
#   - There is no generic token recogniser to fall back on: PASSWORD and GENERIC_BEARER_TOKEN
#     were removed on 2026-08-14 for shredding ordinary content, and reinstating either to catch
#     this would reintroduce that damage across 84,659 rows.
#
# **Do not "fix" this by adding wifi/ssid to the CREDENTIAL_ASSIGNMENT label list.** That
# recogniser's value span runs to the first whitespace, so on this input it would match
# `Wifi: CP` — masking the network name and publishing the password. That is precisely the
# half-masking failure documented above `_CREDENTIAL_ASSIGNMENT_REGEX`. This pattern instead
# spans label → name → secret in one match, so overlap resolution cannot split it.
#
# Deliberately narrow. The value bar is stricter than CREDENTIAL_PAIR's because there is no
# account identifier here to corroborate the guess — only a topic word — so the shape has to
# carry the whole burden: ≥8 chars, at least one letter, and at least one genuine password
# special (`.`, `_` and `-` do NOT count, which is what keeps `Network: prod / 2.4GHz-band` and
# hostnames out).
_NETWORK_CREDENTIAL_REGEX = (
    r"(?i)\b(?:wi-?fi|ssid|wlan|hotspot|network)\b[^\n]{0,24}?[:=]\s*"
    r"[^\s/|]+(?:[ \t]+[^\s/|]+){0,3}"
    r"\s*[/|]\s*"
    r"(?=[^\s/|]{8,})"
    r"(?=[^\s/|]*[A-Za-z])"
    r"(?=[^\s/|]*[!#$%&*+=?^~])"
    r"[^\s/|]+"
)
# A message whose ENTIRE body is one high-entropy token — added 2026-08-15 after this reached
# the agent through `neo-corpus`, one line after a message about a GitLab account:
#
#     Q&123456789012ab
#
# This is the shape no per-message recogniser can reason about, because **the context word is in
# a different message**. `redact()` sees one body at a time and always will: redaction runs at
# write time, per row, and giving it thread context would mean re-architecting the write path
# around conversation state.
#
# So the signal used here is not context but *self-containment*. A chat message whose whole
# content is a single unbroken token containing a letter, a digit AND a password special is
# almost never prose, a filename, an id or a URL — it is someone pasting a secret on its own
# line. That is a heuristic and it is labelled as one; it does not generalise to a secret
# embedded in a sentence, which remains uncovered.
#
# Exclusions are what keep it safe, each for an observed shape in this corpus:
#   - `://` anywhere      → URLs, which routinely carry `?`, `=` and `&`
#   - a bare email        → `sam.h2@x.com` has a letter, a digit and `@`
#   - specials exclude `_ - .` → filenames (`Rethinking_Key_2026.wav`), hostnames, git SHAs and
#     hyphenated ids do not qualify on punctuation alone
#   - **printable ASCII only** → this is the one that measurement caught rather than review.
#     An earlier draft used `\S{10,64}`, which in a corpus that is heavily Mandarin matches an
#     entire ordinary sentence: CJK is written without spaces, so a whole Chinese message is one
#     `\S+` run. It fired 33 times on the corpus, the first being a message beginning `今晚7:`
#     ("tonight at 7"). A secret is ASCII; restricting the class removes the whole FP family.
_STANDALONE_SECRET_REGEX = (
    r"\A\s*"  # noqa: S105 — regex pattern, not a literal
    r"(?![^\s]*://)"
    r"(?![A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\s*\Z)"
    r"(?=[\x21-\x7e]{10,64}\s*\Z)"
    r"(?=\S*[A-Za-z])"
    r"(?=\S*\d)"
    r"(?=\S*[!#$%&*+=?^~])"
    r"[\x21-\x7e]+"
)
# A login handed over as `<sign-in URL> <username> <sep> <secret>` — added 2026-08-16 (Q-043)
# after two of these reached the agent through `neo-corpus` while his outbound was being read
# for the voice build. Neither was sought; they were simply in the sample.
#
#     Here's your AWS login info … - https://<host>/console - <username> / <8-char secret>
#
# Why nothing existing fires. CREDENTIAL_PAIR requires an **email address** left of the
# separator, and these are bare usernames (`asmith`, `bkim`, `jdoe`) — the one structural
# assumption that recogniser makes is exactly what this shape violates. CREDENTIAL_ASSIGNMENT
# needs a label word adjacent to the value; here the label is a console URL on the line before.
# STANDALONE_SECRET needs the body to be *nothing but* the token. So, as with CREDENTIAL_PAIR
# and NETWORK_CREDENTIAL before it, the gap was never a scoring failure — nothing matched at all.
#
# The span runs from the URL through the secret, which means the console URL is masked too. That
# is a deliberate, small loss of context, taken for the same reason NETWORK_CREDENTIAL masks the
# SSID: one span over every part makes it impossible for overlap resolution to publish the half
# that matters. Python's `re` has no variable-length lookbehind, so keeping the URL out of the
# span would mean giving up the anchor entirely.
#
# **Whitespace around the separator is what makes this safe, and it was measured rather than
# reasoned.** The first draft allowed `[/|]` with optional spaces; a URL path is full of slashes,
# so the pattern slid along the URL itself and matched **315** rows whose "secret" was an 85- to
# 115-character path segment. Requiring real whitespace on both sides — which no URL contains —
# took it to **11**, every one of them password-shaped. Same failure family as the `\S{10,64}`
# draft of STANDALONE_SECRET: the shape looked right and the measurement disagreed.
#
# Value bar: 6–40 chars with a letter, a digit AND a genuine password special. `.`, `_` and `-`
# do not count as specials, which is what keeps hostnames and file paths out.
_CONSOLE_LOGIN_REGEX = (
    r"(?i)https?://\S*(?:signin|sign-in|/login|console)\S*"
    r"\s+(?:[-–—:]\s+)?"
    r"[A-Za-z][A-Za-z0-9._@-]{2,30}"
    r"(?:[ \t]+[/|][ \t]+|[ \t]*\n\s*)"
    r"(?=[^\s/|]{6,40})"
    r"(?=[^\s/|]*[A-Za-z])"
    r"(?=[^\s/|]*\d)"
    r"(?=[^\s/|]*[!#$%&*+=?^~()\[\]{}<>])"
    r"[^\s/|]+"
)
# The same handover with an explicit `ID/PW` label instead of a URL — `… the ID/PW for the Basic
# Auth popup is <user> / <pw>`, a staging-site basic-auth credential sent to an ad vendor.
#
# This one carries NO entropy bar, and that is the point: its password is an ordinary English
# word, so every shape test in this module is blind to it. The explicit label is doing the work
# that entropy does elsewhere — the same trade NETWORK_CREDENTIAL makes. `\bis\b` and the 40-char
# window are what keep it from reaching across a sentence. Measured: 2 rows in the visible corpus.
_LOGIN_LABEL_PAIR_REGEX = (
    r"(?i)\bID\s*/\s*PW\b[^\n]{0,40}?\bis\b\s+"
    r"[^\s/|]{2,30}[ \t]*[/|][ \t]*[^\s/|.,;]{3,40}"
)
# `密码: <secret>` — the CREDENTIAL_ASSIGNMENT shape with a Chinese label. Added 2026-08-16 with
# the above, from the same reading.
#
# The label vocabulary of `_CREDENTIAL_ASSIGNMENT_REGEX` is English-only, in a corpus where 31%
# of his outbound is Mandarin and onboarding credentials are routinely handed over in it. This is
# not a new shape — it is the same label→value shape that recogniser already handles safely, in
# the other language, so it rides on the same entity rather than becoming a new one.
#
# It is a separate pattern rather than an extra alternative inside the English regex because
# `\b` cannot express a boundary between two CJK characters: they are both word characters, so
# `\b密码` never matches mid-sentence. The separator is mandatory here for the same reason the
# English pattern requires one — without it, the bare word 密码 in prose would match.
_CREDENTIAL_ASSIGNMENT_CJK_REGEX = (
    r"(?:初始密码|登录密码|登陆密码|账号密码|帳號密碼|密码|密碼|口令)"  # noqa: S105
    r"\s*[:：=]\s*[^\s\"',;，。；]{3,40}"
)
# `密码是<secret>` — the SAME label vocabulary with the natural-Chinese copula instead of
# punctuation. Added 2026-08-31 after two unredacted numeric passwords reached the agent through
# `neo-corpus`, one of them the operator's own outbound.
#
# **The colon form above has never once matched in this corpus.** `密码：`, `密码:`, `密碼：` and
# `密码为` return zero rows each; every real instance uses `是`. That is the difference between how a
# form prints a credential and how a person says one, and the recogniser only knew the former.
#
# This is a SEPARATE pattern rather than `[:：=是为]` in the one above, because the copula cannot
# share that value class. `[^\s"',;，。；]{3,40}` admits CJK, and after `是` the next characters are
# usually ordinary prose — `密码是多少来着` ("what's the password again?"), `忘记密码是吗` ("click
# forgot password?"). Masking those would be a false positive on a question. So the value here must
# be WHOLLY PRINTABLE ASCII and must contain a digit, which prose after `是` never is.
#
# The date guard is not hypothetical: `不过我上次用这个密码是2016年了` ("the last time I used this
# password was 2016") is 1 of the 6 `密码是` rows in the corpus, and a naive fix masks the year.
# Measured before shipping — 6 rows carry `密码是`; 2 are real credentials, 3 are prose, 1 is the
# year. This pattern takes the 2 and leaves the other 4.
_CREDENTIAL_ASSIGNMENT_CJK_COPULA_REGEX = (
    r"(?:初始密码|登录密码|登陆密码|账号密码|帳號密碼|密码|密碼|口令)"  # noqa: S105
    r"[是为]"
    r"(?![0-9]{1,4}\s*[年月日])"
    r"(?=[!-~]{3,40}(?![!-~]))"
    r"(?=[!-~]*[0-9])"
    r"[!-~]{3,40}"
)
# One-time codes — added 2026-08-31, after a THIRD instance reached the agent unredacted in twelve
# days and the harm model changed underneath the earlier decision not to cover them.
#
# **Why this was left alone twice and is covered now.** The 2026-08-19 note argued that every OTP in
# this corpus is dead on arrival — which was true while the corpus ended six weeks in the past. Live
# ingest shipped 2026-08-16; a pull can now land minutes after the mail does, so a code inside its
# 15-minute window is reachable in principle. And the third instance was not a newsletter login: it
# authorises a NEW DEVICE on the domain registrar that controls DNS for the CP estate.
#
# **The feared false-positive population did not materialise, and the reason is the qualified noun.**
# The case against covering this was that a short digit group near the word `code` is the shape
# `CREDIT_CARD` was removed for — order numbers, ticket ids, Meta ad-account numbers. That is a claim
# about the BARE word. Measured over the 220 rows carrying any OTP-ish anchor, these two patterns fire
# 70 times and every hit is a genuine one-time code from an automated sender: zero human, zero
# outbound. The bare word `code` is deliberately NOT a label here.
#
# **`passcode` was measured and REMOVED from the vocabulary.** It produced every false positive in the
# trial: three Zoom meeting passcodes and two physical gate codes. A meeting passcode is shared, reused
# and printed in the invitation it belongs to; masking it breaks a usable invite and protects nothing.
#
# **The token-boundary guards are what stop this half-masking, and they were added because measuring
# caught it doing so.** Docusign's "security code" is a long alphanumeric hex string. A digit-only
# value class matched the digit RUNS INSIDE it — `security code: C<len=4>EDCA3B941CE9FF8BD5…` — masking
# fragments and publishing the letters, which is the exact `CREDENTIAL_ASSIGNMENT` failure this module
# already carries a warning about. `(?<![A-Za-z0-9])` / `(?![A-Za-z0-9])` make the value a whole token,
# so Docusign is now an honest UNCOVERED MISS rather than a mask with holes in it. Fail to cover, never
# half-cover.
#
# **Two arms, because two of the three real instances have no label at all.** The imperative form —
# `enter the following code to authorize your device for NameSilo.com and complete your sign-in:` then
# the digits — carries no OTP noun anywhere. Its gap allowance is wide (100) for the same reason:
# measured, the distance from `following code` to the digits is ~85 characters in NameSilo's mail and
# ~75 in Workflowy's.
#
# **The gap crosses newlines, and that is the fix that mattered most.** The first draft used
# `[^0-9\n]` and caught 24 rows, newest 2026-06-02 — it matched NONE of Workflowy, OneTrust or
# NameSilo, because in all three the code sits on the line AFTER its label. A same-line pattern would
# have passed review and been inert on precisely the mail that prompted it.
# The value: a whole token of 4-to-19 digits, optionally group-separated. The two guards and the
# digit floor each earned their place by a measured regression:
#
# - `(?<![A-Za-z0-9])` / `(?![A-Za-z0-9])` — without them a digit-only class matches the digit RUNS
#   INSIDE an alphanumeric code and publishes the letters. Docusign's `security code:
#   C1234EDCA3B941CE9FF8BD5` half-masked three times in the trial.
# - `(?=(?:[\d\-\s]*\d){4,})` — a first draft made the middle quantifier optional, which let a bare
#   TWO-digit number match and picked up 12 false hits on Namecheap mail in one pass. No one-time code
#   is two digits; the floor is four.
# **Two residuals, measured and deliberately NOT chased.**
#
# 1. `measure_recognisers` cannot show the SHAPE of the secret for this entity, because the span
#    includes the label by this module's convention — so it reports `len=101 [aA9!]` for a sentence
#    plus a six-digit code. That tool's shape discipline is therefore inert here, and the check that
#    does the work is the full-table sweep in `tests/test_fence.py`, which restates a narrower
#    label-arm pattern independently. Worth knowing before trusting a shape line for `ONE_TIME_CODE`.
#
# 2. **Best Buy repeats its code after masking.** Its SMS reads `Your verification code is <code>` and
#    then `@www.bestbuy.com #<the same code>` for browser autofill, so the second copy survives — one
#    row, 2024, a consumer account. Not chased, because the only pattern that reaches it is
#    `#\d{6,8}`, which is an order number, an invoice number and a ticket id everywhere else in this
#    corpus. That is the `CREDIT_CARD` trade exactly, and it loses. **Recorded rather than fixed so
#    the next reader knows it was seen.**
_ONE_TIME_CODE_VALUE = (
    r"(?<![A-Za-z0-9])(?=(?:[\d\-\s]*\d){4,})\d[\d\-\s]{2,17}\d(?![A-Za-z0-9])"
)
_ONE_TIME_CODE_LABEL_REGEX = (
    r"(?i)\b(?:verification|security|one[- ]?time|sign[- ]?in|log[- ]?in|access|"
    r"authoriz\w*|authoris\w*)[ \t]+code\b"
    r"[^0-9]{0,40}?" + _ONE_TIME_CODE_VALUE
)
_ONE_TIME_CODE_IMPERATIVE_REGEX = (
    r"(?i)(?:(?:enter|use|copy|paste)[ \t]+(?:the[ \t]+)?(?:following[ \t]+)?"
    r"code(?:[ \t]+below)?|code[ \t]+below)\b"
    r"[^0-9]{0,100}?" + _ONE_TIME_CODE_VALUE
)

# Payment instructions — added 2026-08-17 (Q-042). A labelled bank wire block reached the agent
# through `neo-corpus`: account name, account number, ACH/wire routing number. His ruling, after
# the exposure was measured: fix the recogniser. He is handling the exposed details with the people
# involved, so this is a filter finding rather than an incident.
#
# **This is the one recogniser here that widens the posture**, from credentials-only to
# credentials-plus-payment-instructions. Everything else in this module removes something that
# authenticates; a routing number authorises instead. It earns the exception because the harm shape
# is the same — a third party who reads it can act with it.
#
# The whole difficulty is that an account number is indistinguishable from an account *identifier*,
# and this corpus is full of the latter. Measured over the visible corpus, "account number" and
# friends appear in **138 rows across 15 chats**: 79 SendGrid customer ids, 23 Meta 15-digit
# ad-account numbers, Rackspace account numbers, and a run of hits that are the word "SWIFT" sitting
# in a news newsletter prose. Exactly **5 rows in 4 chats** are real wire blocks. That ratio is why
# `CREDIT_CARD` was removed (80 hits, 100% false positives, and it masked an ad-revenue fact he
# works with daily) and it is the trap this must not walk back into.
#
# So the discriminator is not the account number. It is the **9-digit ABA routing number beside a
# named account** — a structural check (exactly nine digits) under an unambiguous label, which
# nothing in the false-positive population carries.
#
# Two patterns, and they are gated differently on purpose:
#
#   1. `bank_routing` is safe alone. `routing`/`aba`/`ach` + exactly nine digits on one line is a
#      routing number or nothing; no vendor account id in this corpus looks like that.
#   2. `bank_account` is NOT safe alone — it is the SendGrid/Meta/Rackspace shape. It fires only
#      when the message also carries a payment anchor, enforced in `_WireBlockGated` below.
#
# The span covers label and value in both, which is the `_CREDENTIAL_ASSIGNMENT_REGEX` lesson: a
# recogniser that matches them as two candidates lets overlap resolution keep the label and publish
# the number.
#
# `[ \t]` and not `\s` in the value class, because `\s` matches a newline and a wire block is
# line-separated — the first draft ran the account number's span into `Bank name:` on the line
# below. The value must also start and end on a digit and hold at least six digits in total, which
# is what keeps it off `Account #: 7` and off dates.
_BANK_ROUTING_REGEX = (
    r"(?i)\b(?:routing|aba|rtn|ach)\b[^\n]{0,30}?\b\d{9}\b"
)
_BANK_ACCOUNT_REGEX = (
    r"(?i)\b(?:account|acct|a/c)\s*(?:number|no\.?|#)\s*[:#=]?[ \t]*"
    r"(?=(?:[\d \t-]*\d){6,})\d[\d \t-]{4,22}\d"
)
# What makes an account number a payment instruction rather than a customer id. Deliberately
# payment-specific: `bank` and `account` are NOT here, because they are everywhere in a corpus that
# talks to SendGrid, Meta and Rackspace all week.
_BANK_ANCHOR_REGEX = (
    r"(?i)\b(?:routing|aba|rtn|ach|swift|bic|iban|beneficiar\w+|remittance|"
    r"wire\s+(?:transfer|instruction\w*|details|payment)|bank\s+transfer)\b"
)
_BANK_ANCHOR_RX = re.compile(_BANK_ANCHOR_REGEX)
# NOT added, and the reason is the discipline this module runs on. An `IBAN`/`SWIFT` pattern would
# be structurally easy — IBAN has a checkable shape — and would fire on **zero** rows here: the only
# IBAN/SWIFT-looking hits in the whole corpus are the word "SWIFT" in newsletter copy. A recogniser
# measured at zero is a guess about the future, and this file has been wrong twice about shapes that
# looked obvious. If a foreign wire ever lands, nothing catches it, which is the same acknowledged
# limit as every other unrecognised shape.

_GENERIC_BEARER_TOKEN_REGEX = r"\b[A-Za-z0-9._\-+/=]{16,}\b"  # noqa: S105 — regex pattern, not a literal
# Password value shape: ≥ 8 chars, must contain at least one letter and
# one digit OR one special char. The mix-of-classes requirement filters
# out plain English words at the regex level so the context-boost path
# doesn't over-fire on prose.
_PASSWORD_VALUE_REGEX = (
    r"\b(?=[A-Za-z0-9!@#$%^&*()_+\-=\[\]{};':\"\\|,.<>/?]{8,})"  # noqa: S105 — regex
    r"(?=.*[A-Za-z])(?=.*[\d!@#$%^&*()_+\-=\[\]{};':\"\\|,.<>/?])"
    r"[A-Za-z0-9!@#$%^&*()_+\-=\[\]{};':\"\\|,.<>/?]+\b"
)

# Context word lists. Presidio's PatternRecognizer applies a +0.35 score
# boost when one of these words appears within ~5 tokens of a match
# (empirically verified 2026-05-24). For PASSWORD + GENERIC_BEARER_TOKEN
# we set the base score below SCORE_THRESHOLD so the threshold filter
# silently drops matches that lack context — making context effectively
# mandatory per presidio.md § 8 Decision 4.
_PASSWORD_CONTEXT = ["password", "passwd", "pwd", "pw", "pass", "login", "credentials", "creds"]
# Cloud-secret key names added 2026-07-25 (SURFACE-2026-07-25): the live leak
# was detected at 0.30 and dropped by SCORE_THRESHOLD purely because
# "accessKeyId"/"secretAccessKey" were absent here, so the +0.35 context boost
# never applied. Presidio lowercases + splits context on non-alphanumerics, so
# camelCase JSON keys are matched by their lowercased word parts ("secret",
# "access", "key") — listed individually rather than as camelCase literals.
_BEARER_CONTEXT = [
    "authorization",
    "bearer",
    "token",
    "x-api-key",
    "api-key",
    "apikey",
    "secret",
    "access",
    "key",
    "credentials",
    "creds",
    "accesskeyid",
    "secretaccesskey",
    "sessiontoken",
]
_US_SSN_CONTEXT = ["ssn", "social", "security"]


class _WireBlockGated(PatternRecognizer):
    """A pattern that only counts when the message is a payment instruction.

    Presidio's own way to require context is the score boost: set the base pattern below
    ``SCORE_THRESHOLD`` and let a nearby context word lift it over. **That mechanism is not
    trustworthy for this shape and this module has already been burned by it twice.** It is
    word-adjacency based over a small window, so it depends on how a wire block happens to be
    laid out — and `PASSWORD` / `GENERIC_BEARER_TOKEN` were both removed after measurement
    showed the same enhancer over-firing on prose while missing real secrets. A gate that
    depends on a scoring heuristic landing is the kind of gate this codebase does not accept.

    So the gate is deterministic and over the whole text: no payment anchor anywhere in the
    message, no detection at all, regardless of layout, ordering or line breaks. An account
    number above its routing number is gated the same as one below it, which a forward lookahead
    in the regex could not have managed.
    """

    def analyze(self, text: str, entities: Any, nlp_artifacts: Any = None) -> list[Any]:  # noqa: ANN401
        if not _BANK_ANCHOR_RX.search(text):
            return []
        return super().analyze(text, entities, nlp_artifacts)


class RedactionItem(BaseModel):
    """One redaction event recorded against an output span."""

    entity_type: str
    start: int
    end: int
    score: float
    operator: str = "replace"


class RedactedText(BaseModel):
    """Output of the pre-egress redaction pipeline.

    ``credentials_redacted`` defaults to ``True`` because the only way to
    construct one is through :func:`redact` (or by hand in a test). The
    model router (``core/router.py``) refuses any ``LLMRequest`` whose
    ``credentials_redacted`` is False, so a ``RedactedText`` always
    encodes the "this text has been through the filter" guarantee.

    .. warning::
       The guarantee is **"the filter ran"**, NOT **"no credentials
       remain"**. A pass-through with zero detections is indistinguishable
       from a successful scrub, so an unrecognized credential shape still
       reaches egress. The 2026-07-25 AWS-key leak was exactly this: the
       flag read ``True`` while the key sat in the text verbatim.

       Adding recognizers narrows the gap; it does not close the class.
       **Do not treat ``credentials_redacted is True`` as proof of a clean
       body.**

       What *was* settled (2026-07-25, operator decision): pass this
       ``RedactedText`` to ``LLMRequest(redaction=...)`` rather than
       hardcoding ``credentials_redacted=True`` at the dispatch site. The
       router then *derives* the gate from this object, so "the filter ran"
       is structurally true instead of caller-asserted — a call site that
       skipped ``redact()`` has no ``RedactedText`` to hand over and is
       refused at construction. Whether the flag should further assert
       *cleanliness* (entropy/shape screening, blocking on suspicion) was
       weighed and deliberately deferred: false positives on base64 blobs,
       hashes and git SHAs would refuse legitimate work. See
       ``SURFACE-2026-07-25-REDACTION-MISSES-CLOUD-KEYS-FLAG-OVERSTATES-GUARANTEE``.
    """

    text: str
    redaction_metadata: list[RedactionItem]
    credentials_redacted: bool = True


class Redactor:
    """Holds the analyzer + anonymizer singletons.

    Instantiated lazily via :func:`_get_redactor`. Constructing
    ``AnalyzerEngine`` is ~2s (spaCy NLP model load); subsequent
    ``redact()`` calls are ~2-5ms on a 200-char message.
    """

    def __init__(self) -> None:
        self._analyzer = AnalyzerEngine()
        _register_custom_recognizers(self._analyzer)
        # AnonymizerEngine is a partial-stub class in presidio-anonymizer's
        # py.typed package; the constructor lacks a typed signature.
        self._anonymizer = AnonymizerEngine()  # type: ignore[no-untyped-call]

    def redact(self, text: str) -> RedactedText:
        analyzer_results: list[Any] = self._analyzer.analyze(text=text, language="en")
        filtered = [
            r
            for r in analyzer_results
            if r.score >= SCORE_THRESHOLD and r.entity_type in REDACT_ENTITIES
        ]
        filtered = _resolve_overlaps(filtered)
        if not filtered:
            return RedactedText(text=text, redaction_metadata=[])
        anonymized = self._anonymizer.anonymize(text=text, analyzer_results=filtered)
        items = [
            RedactionItem(
                entity_type=item.entity_type,
                start=item.start,
                end=item.end,
                score=_score_for_item(item, filtered),
                operator=item.operator,
            )
            for item in anonymized.items
        ]
        return RedactedText(text=anonymized.text, redaction_metadata=items)


def _score_for_item(item: Any, analyzer_results: list[Any]) -> float:
    """Recover the analyzer score for an anonymizer ``OperatorResult``.

    Presidio's anonymizer drops the score field from its ``OperatorResult``
    items, but the redaction metadata is more useful with it preserved.
    Match on ``(entity_type, start)`` — the anonymizer left-anchors
    replacements so ``start`` is preserved, but ``end`` is rewritten to
    the post-replacement coordinate and no longer matches the analyzer's
    original span.
    """
    for r in analyzer_results:
        if r.entity_type == item.entity_type and r.start == item.start:
            return float(r.score)
    return 0.0


_REDACTOR: Redactor | None = None


def _get_redactor() -> Redactor:
    global _REDACTOR
    if _REDACTOR is None:
        _REDACTOR = Redactor()
    return _REDACTOR


def redact(text: str) -> RedactedText:
    """Run pre-egress credentials-redaction on ``text``.

    The returned :class:`RedactedText` carries ``credentials_redacted=True``,
    which is the flag the model router checks before dispatching an
    ``LLMRequest``.
    """
    return _get_redactor().redact(text)


SPOILER_PLACEHOLDER = "<SPOILER>"


def utf16_span_to_chars(text: str, offset: int, length: int) -> tuple[int, int]:
    """Convert a Telegram entity offset/length (UTF-16 code units) to Python char indices.

    **This conversion is the whole correctness risk of spoiler masking, and it is not
    theoretical here.** Telegram measures entity offsets in UTF-16 code units; Python measures
    string indices in code points. The two agree only while every preceding character is in the
    BMP. This corpus is Mandarin-heavy — CJK is BMP, so it agrees there — but it is also
    emoji-heavy, and every emoji is a surrogate pair worth two UTF-16 units and one Python char.
    So a spoiler after a single 🙏 lands one character late, after two 🙏 two characters late,
    and the mask slides off the secret while still looking plausible.

    That is the half-masking failure this module has already shipped once (see
    ``CREDENTIAL_ASSIGNMENT``, whose value span stopped at the first whitespace and published the
    password while masking the label). Sliced through the UTF-16 encoding instead of guessed.
    """
    encoded = text.encode("utf-16-le")
    start = len(encoded[: offset * 2].decode("utf-16-le", errors="ignore"))
    end = len(encoded[: (offset + length) * 2].decode("utf-16-le", errors="ignore"))
    return start, end


def mask_spoilers(text: str, spans: list[tuple[int, int]]) -> str:
    """Replace each ``(start, end)`` char span with ``<SPOILER>``.

    **Why this is not a recogniser, and must not be built as one.** Every other entry in
    ``REDACT_ENTITIES`` is a pattern that guesses, from the text alone, that something is a
    secret — which is why each one needs measuring against the whole corpus before it ships, and
    why two were removed after over-firing on ordinary prose. A spoiler carries no pattern at
    all: the text inside it is arbitrary, and the *only* signal is that a human deliberately
    marked it hidden. So there is nothing to match and nothing to tune, and the false-positive
    rate is zero by construction — the sender declared the intent.

    The corollary is the uncomfortable half: **this cannot be measured over the existing
    corpus.** Telegram carries the spoiler as a ``MessageEntitySpoiler`` alongside the message,
    and ``to_event`` stored ``message.message`` — the plain text — and dropped the entities. So
    no historical row records that any of its text was ever spoilered, and no re-run of the
    redaction sweep can recover it. Coverage starts at the first pull after this shipped;
    everything before it is unmarked and unmarkable without re-fetching from Telegram.

    Applied right-to-left so that replacing a span cannot invalidate the offsets of an earlier
    one.
    """
    for start, end in sorted(spans, reverse=True):
        if 0 <= start < end <= len(text):
            text = text[:start] + SPOILER_PLACEHOLDER + text[end:]
    return text


def spoiler_items(text: str) -> list[RedactionItem]:
    """Locate the ``<SPOILER>`` masks in a FINAL redacted body, as metadata.

    Deliberately derived from the finished text rather than carried through from the offsets
    ``mask_spoilers`` used: those were indices into the pre-Presidio string, and Presidio then
    rewrote the text around them, so carrying them forward would record spans that no longer
    point at what they claim to. ``redaction_meta`` is read as a description of the stored body.
    """
    items: list[RedactionItem] = []
    pos = text.find(SPOILER_PLACEHOLDER)
    while pos != -1:
        items.append(
            RedactionItem(
                entity_type="SPOILER",
                start=pos,
                end=pos + len(SPOILER_PLACEHOLDER),
                score=1.0,
            )
        )
        pos = text.find(SPOILER_PLACEHOLDER, pos + 1)
    return items


def redact_image(image_bytes: bytes) -> bytes:
    """Stub for multimodal pre-egress redaction (deferred to M3+).

    The signature is accepted now so future callers (briefing, drafting)
    don't need to branch on text-vs-image. Returns the input unchanged.
    """
    return image_bytes


def _entity_priority(entity_type: str) -> int:
    """Lower index = more specific. Default Presidio entities rank after
    all custom entities."""
    try:
        return _CUSTOM_ENTITY_PRIORITY.index(entity_type)
    except ValueError:
        return len(_CUSTOM_ENTITY_PRIORITY)


def _resolve_overlaps(results: list[Any]) -> list[Any]:
    """Drop lower-priority detections whose span overlaps a kept one.

    Sort key: (start, -score, priority-of-entity-type, -span_length).
    Lower priority-index wins (custom recognizers beat default entities).
    """
    sorted_results = sorted(
        results,
        key=lambda r: (
            r.start,
            -r.score,
            _entity_priority(r.entity_type),
            -(r.end - r.start),
        ),
    )
    kept: list[Any] = []
    for r in sorted_results:
        overlaps = False
        for k in kept:
            if r.start < k.end and r.end > k.start:
                overlaps = True
                break
        if not overlaps:
            kept.append(r)
    return kept


def _register_custom_recognizers(analyzer: AnalyzerEngine) -> None:
    """Register WP13's custom + WP10-inherited custom recognizers."""
    # Inherited verbatim from WP10 Phase 2.
    analyzer.registry.add_recognizer(
        PatternRecognizer(
            supported_entity="JWT",
            patterns=[Pattern(name="jwt_triple", regex=_JWT_REGEX, score=0.8)],
        )
    )
    analyzer.registry.add_recognizer(
        PatternRecognizer(
            supported_entity="GITHUB_PAT",
            patterns=[
                Pattern(name="ghp_classic", regex=_GITHUB_PAT_CLASSIC_REGEX, score=0.9),
                Pattern(name="ghp_fine", regex=_GITHUB_PAT_FINE_REGEX, score=0.9),
            ],
        )
    )
    # New 2026-07-25 (SURFACE-2026-07-25). Context-FREE by design: the AKIA/ASIA
    # prefix is self-identifying, so this fires at 0.9 with no nearby context
    # word required — the opposite of the GENERIC_BEARER_TOKEN posture below.
    analyzer.registry.add_recognizer(
        PatternRecognizer(
            supported_entity="AWS_ACCESS_KEY_ID",
            patterns=[Pattern(name="aws_akid", regex=_AWS_ACCESS_KEY_ID_REGEX, score=0.9)],
        )
    )
    # Anthropic keys, same posture. See _ANTHROPIC_API_KEY_REGEX for why this was missing.
    analyzer.registry.add_recognizer(
        PatternRecognizer(
            supported_entity="ANTHROPIC_API_KEY",
            patterns=[Pattern(name="sk_ant", regex=_ANTHROPIC_API_KEY_REGEX, score=0.9)],
        )
    )
    # Also context-free by design — the KEY NAME is the identifying signal and
    # it is inside the match itself, so no word-adjacency boost is involved.
    analyzer.registry.add_recognizer(
        PatternRecognizer(
            supported_entity="CLOUD_SECRET",
            patterns=[Pattern(name="cloud_secret_kv", regex=_CLOUD_SECRET_VALUE_REGEX, score=0.9)],
        )
    )
    # New in WP13.
    analyzer.registry.add_recognizer(
        PatternRecognizer(
            supported_entity="URL_WITH_CREDENTIALS",
            patterns=[Pattern(name="url_with_creds", regex=_URL_WITH_CREDENTIALS_REGEX, score=0.9)],
        )
    )
    analyzer.registry.add_recognizer(
        PatternRecognizer(
            supported_entity="US_SSN",
            patterns=[Pattern(name="ssn_backstop", regex=_US_SSN_BACKSTOP_REGEX, score=0.85)],
            context=_US_SSN_CONTEXT,
        )
    )
    # Replaces the former context-boosted PASSWORD + GENERIC_BEARER_TOKEN pair, both of which
    # were removed after measurement on real corpus rows. Their shared failure mode: a bare
    # token/word *shape* lifted over threshold by any credential-ish word within ~5 tokens.
    # In an IT-support corpus that context is everywhere ("pw", "login", "credentials", "key"),
    # so they shredded ordinary content — `https://feeds.transistor.fm/tcpdaily` became
    # `https://<GENERIC_BEARER_TOKEN>`, `[audio: Rethinking_Key_Hypotheses_refined.wav]` became
    # `[audio: <GENERIC_BEARER_TOKEN>]` — while missing the short real secrets entirely
    # (`zqx2026`, `zion7`, `971683` are all below the 8-char/class-mix bar).
    #
    # Context-free label->value matching is both safer and strictly better at the real target.
    analyzer.registry.add_recognizer(
        PatternRecognizer(
            supported_entity="CREDENTIAL_ASSIGNMENT",
            patterns=[
                Pattern(name="cred_kv", regex=_CREDENTIAL_ASSIGNMENT_REGEX, score=0.9),
                # Same shape, Chinese label. Separate pattern because `\b` cannot mark a
                # boundary between two CJK characters — see the regex comment.
                Pattern(name="cred_kv_cjk", regex=_CREDENTIAL_ASSIGNMENT_CJK_REGEX, score=0.9),
                # The copula form. Separate pattern, same entity — see the regex comment for why it
                # cannot share the colon form's value class.
                Pattern(
                    name="cred_kv_cjk_copula",
                    regex=_CREDENTIAL_ASSIGNMENT_CJK_COPULA_REGEX,
                    score=0.9,
                ),
            ],
        )
    )
    # New 2026-08-14. The unlabelled human shape — `<account>/<secret>` — which
    # CREDENTIAL_ASSIGNMENT cannot reach because there is no label word to anchor on.
    # Context-free at 0.9; the account identifier inside the match is the signal.
    analyzer.registry.add_recognizer(
        PatternRecognizer(
            supported_entity="CREDENTIAL_PAIR",
            patterns=[
                Pattern(name="account_sep_secret", regex=_CREDENTIAL_PAIR_REGEX, score=0.9),
            ],
        )
    )
    # New 2026-08-16 (Q-043). A login handed over after a sign-in URL, or behind an explicit
    # `ID/PW` label. CREDENTIAL_PAIR cannot reach either: it requires an email left of the
    # separator, and these are bare usernames. Context-free at 0.9; the sign-in URL (or the
    # label) is inside the match, and the span covers URL, username and secret so overlap
    # resolution cannot mask one part and publish another.
    analyzer.registry.add_recognizer(
        PatternRecognizer(
            supported_entity="CONSOLE_LOGIN",
            patterns=[
                Pattern(name="console_url_login", regex=_CONSOLE_LOGIN_REGEX, score=0.9),
                Pattern(name="idpw_label_login", regex=_LOGIN_LABEL_PAIR_REGEX, score=0.9),
            ],
        )
    )
    # New 2026-08-15. `<network label>: <ssid> / <secret>` — the shape CREDENTIAL_ASSIGNMENT
    # cannot anchor on (its label list has no network words, and the token after the colon is
    # the SSID) and CREDENTIAL_PAIR cannot reach (it requires an email left of the separator).
    # Context-free at 0.9; the span covers label, name and secret so it cannot half-mask.
    analyzer.registry.add_recognizer(
        PatternRecognizer(
            supported_entity="NETWORK_CREDENTIAL",
            patterns=[
                Pattern(name="network_label_secret", regex=_NETWORK_CREDENTIAL_REGEX, score=0.9),
            ],
        )
    )
    # New 2026-08-17 (Q-042). A labelled bank wire block — the first entry here that is not a
    # credential, and the only one that widens the posture.
    #
    # The two patterns are gated differently and that asymmetry IS the design. A `routing`/`aba`/
    # `ach` label beside exactly nine digits is self-identifying, so it fires context-free at 0.9
    # like every other recogniser in this file. A labelled account NUMBER is not self-identifying at
    # all — it is the SendGrid, Meta and Rackspace shape, 133 rows of it in this corpus — so it fires
    # only through `_WireBlockGated`, which requires a payment anchor somewhere in the same message.
    #
    # Both patterns sit on the same entity because they are two halves of one wire block, and
    # splitting them would mean two placeholders in one paragraph telling the reader less.
    analyzer.registry.add_recognizer(
        PatternRecognizer(
            supported_entity="BANK_DETAILS",
            patterns=[Pattern(name="bank_routing", regex=_BANK_ROUTING_REGEX, score=0.9)],
        )
    )
    analyzer.registry.add_recognizer(
        _WireBlockGated(
            supported_entity="BANK_DETAILS",
            patterns=[Pattern(name="bank_account", regex=_BANK_ACCOUNT_REGEX, score=0.9)],
        )
    )
    # New 2026-08-31. One-time codes, two arms on one entity: a qualified OTP noun in label
    # position, and the imperative form that carries no noun at all. Both context-free at 0.9 —
    # the label IS the context, and the bare word `code` is not in the vocabulary.
    analyzer.registry.add_recognizer(
        PatternRecognizer(
            supported_entity="ONE_TIME_CODE",
            patterns=[
                Pattern(name="otp_label", regex=_ONE_TIME_CODE_LABEL_REGEX, score=0.9),
                Pattern(name="otp_imperative", regex=_ONE_TIME_CODE_IMPERATIVE_REGEX, score=0.9),
            ],
        )
    )
    # New 2026-08-15. A body that is nothing but one high-entropy token. Scored at 0.9 and
    # context-free of necessity — the only context that would identify it lives in an adjacent
    # MESSAGE, which the per-row write path cannot see. Self-containment is the signal instead;
    # see the regex comment for why that is a heuristic rather than a shape guarantee.
    analyzer.registry.add_recognizer(
        PatternRecognizer(
            supported_entity="STANDALONE_SECRET",
            patterns=[
                Pattern(name="whole_body_token", regex=_STANDALONE_SECRET_REGEX, score=0.9),
            ],
        )
    )
