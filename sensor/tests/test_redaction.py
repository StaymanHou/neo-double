"""Unit tests for the redaction recognisers.

Separate from ``test_fence.py``, which sweeps the real corpus. These run on synthetic strings
only, so they can assert on the *value* of a match — something the corpus sweep deliberately
never does, because printing a real match is how a leak becomes two leaks.

**What these tests cannot tell you.** A recogniser that passes every case here can still be
wrong on the corpus: the failure mode that matters for at-rest redaction is the false positive,
and its rate is a property of 84,659 real bodies, not of the examples someone thought to write
down. `CREDIT_CARD` passed its unit tests and fired 80 times on the corpus with a 100% FP rate.
Measure with ``python -m neo_sensor.measure_recognisers`` before committing a re-redaction.
"""

from __future__ import annotations

import pytest

from neo_sensor.redaction import (
    SPOILER_PLACEHOLDER,
    mask_spoilers,
    redact,
    spoiler_items,
    utf16_span_to_chars,
)

# --- The two shapes that reached the agent on 2026-08-15 ----------------------------------


def test_network_credential_is_removed() -> None:
    """`Guest Wifi: CP Guest / iBbAX~mG8-a+` — the live guest-wifi password (Q-037).

    Reached the agent through `neo-corpus` because CREDENTIAL_ASSIGNMENT's label list has no
    network words and CREDENTIAL_PAIR requires an email left of the separator.
    """
    out = redact("Guest Wifi: CP Guest / iBbAX~mG8-a+")
    assert "iBbAX~mG8-a+" not in out.text
    assert "NETWORK_CREDENTIAL" in out.text


def test_standalone_secret_is_removed() -> None:
    """`Q&123456789012ab` — the bare token posted as its own message (Q-035).

    Its context word (`GitLab account`) was in the *previous* message, which no per-row
    recogniser can see. Self-containment is the signal instead.
    """
    out = redact("Q&123456789012ab")
    assert "Q&123456789012ab" not in out.text
    assert "STANDALONE_SECRET" in out.text


def test_network_credential_does_not_half_mask() -> None:
    """The span must cover label AND secret.

    The documented failure of the old PASSWORD recogniser was matching label and value as two
    candidates, keeping whichever sorted first, and so masking `Password` while publishing
    `zqx2026`. A wider span cannot fail that way — assert the secret is gone, not merely that
    something was replaced.
    """
    out = redact("SSID: KM-Office / Tr0ub4dor&3xample")
    assert "Tr0ub4dor&3xample" not in out.text


# --- False-positive guards. Each is a shape observed in this corpus. -----------------------


@pytest.mark.parametrize(
    "text",
    [
        # A URL alone: routinely carries ? & = and would otherwise look high-entropy.
        "https://tagassistant.google.com/#/?id=GTM-ABCD123&url=https%3A%2F%2Fexample.com",
        # A bare email with a digit in it — letter, digit and `@` all present.
        "sam.h2@example.com",
        # A filename: `_` and `.` are deliberately NOT counted as password specials.
        "Rethinking_Key_Hypotheses_2026.wav",
        # A git SHA / hex id: no special character at all.
        "a3f8b9c2d1e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8",
        # A support case number: no letter.
        "102700000019",
        # A hyphenated identifier.
        "GTM-ABCD123-2026",
        # Ordinary prose that happens to be one line.
        "Deployed to production at 14:35, all green",
    ],
)
def test_standalone_secret_leaves_ordinary_bodies_alone(text: str) -> None:
    assert redact(text).text == text


@pytest.mark.parametrize(
    "text",
    [
        # Network status chatter with a slash — value too short and no password special.
        "Wifi: 5GHz / 2.4GHz",
        # A slash used as "or" between two plain words.
        "Network: production / staging",
        # A hostname after the separator: `.` and `-` are not password specials.
        "SSID: guest / ap-2.office.local",
    ],
)
def test_network_credential_leaves_ordinary_bodies_alone(text: str) -> None:
    assert redact(text).text == text


# --- Regressions on the shapes that were already covered ----------------------------------


def test_credential_pair_still_fires() -> None:
    """The 2026-08-14 shape must not regress when new recognisers are added."""
    out = redact("Bit.ly alice@example.com/ Bluebird777!!!")
    assert "Bluebird777!!!" not in out.text


def test_credential_assignment_still_fires_and_keeps_its_label_masked() -> None:
    out = redact("Dashboard: bit.ly/3AbCdEf | Password: zqx2026")
    assert "zqx2026" not in out.text


def test_identity_still_passes_through() -> None:
    """Identity is deliberately NOT redacted — translate cannot work without it.

    Guards against a new recogniser quietly widening the allow-list.
    """
    text = "Hi Alice, can you confirm with operator@example.com before Friday?"
    assert redact(text).text == text


# --- Q-043: a login handed over after a sign-in URL, or behind an ID/PW label ---------------


def test_console_login_after_sign_in_url_is_removed() -> None:
    """The 2026-08-16 shape. CREDENTIAL_PAIR structurally cannot reach it — it requires an
    email left of the separator, and this is a bare username."""
    out = redact(
        "Here's your AWS login info - https://acme.signin.aws.amazon.com/console - "
        "jdoe / Xk7P[2m}"
    )
    assert "Xk7P[2m}" not in out.text
    assert "CONSOLE_LOGIN" in out.text


def test_console_login_across_separate_lines_is_removed() -> None:
    """The Outlook form: URL, then username, then secret, each on its own line. No separator
    character at all, which is why a slash-anchored pattern would miss it."""
    out = redact("https://acme.signin.aws.amazon.com/console\n\nacmecloud\n\n47qRt9!b")
    assert "47qRt9!b" not in out.text


def test_console_login_masks_the_username_too() -> None:
    """One span over URL, username and secret. A match that published either half of the pair
    would be the half-masking failure documented above `_CREDENTIAL_ASSIGNMENT_REGEX`."""
    out = redact("https://acme.signin.aws.amazon.com/console - asmith / mTK48W)[")
    assert "mTK48W)[" not in out.text
    assert "asmith" not in out.text


def test_id_pw_label_removes_a_password_with_no_entropy() -> None:
    """`media` passes no shape test in this module; the explicit label is what carries it."""
    out = redact("The ID/PW for the Basic Auth popup is acme / media")
    assert "acme / media" not in out.text


def test_cjk_password_label_is_removed() -> None:
    """The label vocabulary was English-only in a corpus that is 31% Mandarin outbound."""
    out = redact("https://outlook.office.com/mail/ - bob.n@example.com 初始密码: Q&123456789012ab")
    assert "Q&123456789012ab" not in out.text


@pytest.mark.parametrize(
    "text",
    [
        # The failure the first draft actually had: a URL path is full of slashes, so a pattern
        # allowing `/` without surrounding whitespace slid along the URL and matched an 85-char
        # path segment as the "secret". 315 rows over the visible corpus, all of them wrong.
        "See https://docs.aws.amazon.com/console/latest/UserGuide/what-is-service.html for more",
        "https://admanager.google.com/1234#creatives/creative_template/detail/template_id=12178",
        # A sign-in URL with ordinary prose after it — no pair, so nothing to mask.
        "You can log in at https://acme.signin.aws.amazon.com/console when you get a chance",
        # A username with a non-secret after it: no digit, no password special.
        "https://acme.signin.aws.amazon.com/console - bkim / readonly",
        # Chinese prose containing the label word but no assignment.
        "这个密码我等下发给你，先把账号建好",
    ],
)
def test_console_login_leaves_ordinary_bodies_alone(text: str) -> None:
    assert redact(text).text == text


# ------------------------------------------------------------------ BANK_DETAILS (Q-042)
#
# The one recogniser here that is not about credentials. His ruling, 2026-08-17, after a labelled
# wire block reached the agent through `neo-corpus`.
#
# **The false-positive tests below matter more than the positive ones**, and the ratio says why:
# over the visible corpus, "account number" and friends appear in 138 rows across 15 chats, of which
# exactly 5 are real wire blocks. The other 133 are SendGrid customer ids, Meta 15-digit ad-account
# numbers, Rackspace account numbers and the word "SWIFT" in newsletter prose. `CREDIT_CARD` was
# removed from this module for firing 80 times with a 100% false-positive rate on that same
# population; this must not walk back into it.


def test_bank_routing_number_is_removed() -> None:
    """`routing` + exactly nine digits. Self-identifying, so it needs no anchor."""
    out = redact("Routing (ACH): 123456780")
    assert "123456780" not in out.text


def test_bank_wire_block_removes_both_number_and_routing() -> None:
    """The real shape: a multi-line block. Both fields go; the bank's name is not a secret."""
    body = (
        "Please wire the deposit.\n"
        "Bank name: Acme Bank\n"
        "Account name: Acme Media LLC\n"
        "Account number: 1000123456789\n"
        "Routing (ach): 987654320\n"
    )
    out = redact(body)
    assert "1000123456789" not in out.text
    assert "987654320" not in out.text
    assert "Acme Bank" in out.text, "the bank's name identifies no account and should survive"


def test_bank_account_is_gated_on_a_payment_anchor_not_on_line_order() -> None:
    """Routing ABOVE the account number gates it exactly as routing below it does.

    A forward lookahead in the regex could not do this, which is why the gate is a whole-text
    check in `_WireBlockGated` rather than a scoring heuristic or a lookahead.
    """
    out = redact("ACH routing 987654320\nAccount no. 1000123456789")
    assert "1000123456789" not in out.text
    assert "987654320" not in out.text


@pytest.mark.parametrize(
    "text",
    [
        # The 79-row SendGrid population: a customer id under an `account` label, no payment
        # anything in the message.
        "Your SendGrid account number 90210665 has been upgraded to the Pro plan.",
        # The 23-row Meta population. This is the exact shape CREDIT_CARD was removed for masking:
        # Luhn validates it, and it is an ad-revenue fact the operator works with daily.
        "Acme News Ad Account (123456789012347) spent $412 yesterday",
        "Meta for Business — account number: 123456789012347 — billing threshold reached",
        # Rackspace: seven digits, and no anchor.
        "Rackspace account no. 1042118 is past due, please review the invoice.",
        # "SWIFT" as an ordinary word in newsletter copy — an anchor with no account beside it.
        "a news: the SWIFT network saw record volume, and banks are moving faster than regulators",
        # A nine-digit number with no routing label at all.
        "The article got 123456780 pageviews last quarter, which nobody believes",
        # An anchor present, but the number is too short to be an account and is not labelled as one.
        "For the wire transfer, our beneficiary is Acme Media and the ref is 4471",
        # An invoice number under an anchor-bearing message: labelled, but not as an account.
        "Wire transfer sent. Invoice number: 1000123456789 for your records.",
    ],
)
def test_bank_details_leaves_account_identifiers_alone(text: str) -> None:
    assert redact(text).text == text


# ------------------------------------------------------------------- Telegram spoilers
#
# His request, 2026-08-21: redact text sent in Telegram's spoiler format. Unlike every other
# entry in this module there is no pattern to match — the text inside a spoiler is arbitrary and
# the only signal is that a human marked it hidden. So what these tests pin is not detection
# accuracy but the one thing that CAN go wrong: the offset arithmetic.


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        # Plain ASCII: UTF-16 units and Python chars agree, so this passes even if the
        # conversion is a no-op. It is here to catch the conversion breaking the easy case.
        ("the password is hunter2 thanks", "hunter2"),
        # One emoji before the span. Every emoji is a surrogate pair — two UTF-16 units, one
        # Python char — so a naive read of Telegram's offset lands ONE character late and
        # publishes the first character of the secret while still looking masked.
        ("🙏 the code is hunter2 ok", "hunter2"),
        # Two emoji: the drift grows with the count, which is what makes it a sliding failure
        # rather than an off-by-one somebody would notice.
        ("🙏🙏 pw hunter2", "hunter2"),
        # CJK is inside the BMP, so offsets agree and the conversion must not disturb it. This
        # corpus is Mandarin-heavy; a conversion that "fixed" CJK would break the common case.
        ("密码是 hunter2 谢谢", "hunter2"),
        # Emoji AND CJK together, which is how this corpus actually reads.
        ("🙏 密码是 hunter2 谢谢🙏", "hunter2"),
    ],
)
def test_a_spoiler_span_is_masked_exactly(text: str, secret: str) -> None:
    """The mask covers the secret and nothing but the secret.

    Half-masking is the specific failure this guards: `CREDENTIAL_ASSIGNMENT` once matched
    `Wifi: CP` on `Guest Wifi: CP Guest / <secret>`, masking the network name and publishing the
    password. A span that lands one character late does the same thing and reads as a success.
    """
    char_start = text.index(secret)
    # The offsets Telegram would send, derived rather than hand-counted.
    offset = len(text[:char_start].encode("utf-16-le")) // 2
    length = len(secret.encode("utf-16-le")) // 2

    start, end = utf16_span_to_chars(text, offset, length)
    assert text[start:end] == secret

    masked = mask_spoilers(text, [(start, end)])
    assert secret not in masked
    assert SPOILER_PLACEHOLDER in masked
    # Nothing outside the span was touched.
    assert masked == text[:char_start] + SPOILER_PLACEHOLDER + text[char_start + len(secret) :]


def test_the_naive_offset_would_have_leaked_the_first_character() -> None:
    """Pin the bug, not just the fix — so a later 'simplification' cannot quietly restore it.

    If someone replaces the conversion with `text[offset:offset+length]`, this test is the one
    that explains why the output still looked fine.
    """
    text = "🙏 the code is hunter2 ok"
    offset = len(text[: text.index("hunter2")].encode("utf-16-le")) // 2
    naive = text[offset : offset + len("hunter2")]
    assert naive != "hunter2"
    assert mask_spoilers(text, [(offset, offset + len("hunter2"))]) != mask_spoilers(
        text, [utf16_span_to_chars(text, offset, len("hunter2"))]
    )


def test_multiple_spoilers_in_one_message_all_survive_masking() -> None:
    """Applied right-to-left, so masking one span cannot invalidate an earlier one's offsets."""
    text = "user alice pass hunter2 and totp 123456 ok"
    spans = [
        (text.index("hunter2"), text.index("hunter2") + len("hunter2")),
        (text.index("123456"), text.index("123456") + len("123456")),
    ]
    masked = mask_spoilers(text, spans)
    assert "hunter2" not in masked
    assert "123456" not in masked
    assert masked.count(SPOILER_PLACEHOLDER) == 2


def test_spoiler_metadata_describes_the_stored_body() -> None:
    """`redaction_meta` is read as a description of `body_redacted`, so it is derived from it.

    Carrying the pre-Presidio offsets forward would record spans pointing at text that the
    anonymizer has since rewritten — truthful-looking metadata about the wrong string.
    """
    final = redact(mask_spoilers("pw hunter2 and key hunter3", [(3, 10), (19, 26)])).text
    items = spoiler_items(final)
    assert [i.entity_type for i in items] == ["SPOILER", "SPOILER"]
    for item in items:
        assert final[item.start : item.end] == SPOILER_PLACEHOLDER


def test_a_spoiler_is_masked_before_presidio_and_survives_it() -> None:
    """Order matters: mask, then redact. The placeholder must come out the other side intact.

    If Presidio rewrote or split `<SPOILER>`, the metadata derived from the final text would find
    nothing and the run would report zero spoilers while having masked some.
    """
    out = redact(mask_spoilers("the aws key is AKIAIOSFODNN7EXAMPLE", [(15, 35)]))
    assert SPOILER_PLACEHOLDER in out.text
    assert "AKIAIOSFODNN7EXAMPLE" not in out.text


# ── ONE_TIME_CODE — added 2026-08-31 ────────────────────────────────────────────
#
# Each of these pins a defect that measuring caught in a pattern that had already been reasoned
# through and looked right. They are behavioural, not cosmetic: three of the four would have
# shipped.


@pytest.mark.parametrize(
    ("body", "why"),
    [
        (
            "Your verification code is:\n\n418203\n\n"
            "Please note that this code expires in 15 minutes.",
            "the code sits on the line AFTER its label -- the first draft used [^0-9\\n] and "
            "matched none of the three real instances that prompted this recogniser",
        ),
        (
            "We noticed a login attempt to your account from a new device. Please enter the "
            "following code to authorize your device for NameSilo.com and complete your "
            "sign-in:\n\n530117",
            "the imperative arm: there is no OTP noun anywhere in this mail, and the gap from "
            "'following code' to the digits is ~85 characters",
        ),
        (
            "To finish logging in, copy the code below and paste it into the form where we "
            "asked you for it:\n\n1234-5678-9012",
            "group-separated 12-digit code",
        ),
        ("Your Ubiquiti verification code is: 602938", "the ordinary same-line label form"),
    ],
)
def test_one_time_code_is_removed(body: str, why: str) -> None:
    assert "ONE_TIME_CODE" in redact(body).text, why


def test_one_time_code_does_not_half_mask_an_alphanumeric_code() -> None:
    """Docusign's `security code` is a long alphanumeric hex string.

    A digit-only value class matches the digit RUNS INSIDE it, masking fragments and publishing
    the letters -- the exact `CREDENTIAL_ASSIGNMENT` failure this module already warns about, and
    it fired three times in the trial. The token-boundary guards make this an honest UNCOVERED
    MISS instead. **Fail to cover, never half-cover** -- so the assertion is that the body is
    untouched, and if a later change starts covering Docusign properly this test should be
    rewritten rather than deleted.
    """
    body = (
        "Visit Docusign.com, click 'Access Documents', and enter the security code: "
        "C1234EDCA3B941CE9FF8BD5"
    )
    out = redact(body).text
    assert out == body
    assert "C1234EDCA3B941CE9FF8BD5" in out


@pytest.mark.parametrize(
    ("body", "why"),
    [
        (
            "Meeting ID: 123 4567 8901Passcode: 123456",
            "`passcode` was MEASURED and removed from the vocabulary: it produced every false "
            "positive in the trial. A Zoom passcode is shared, reused and printed in the "
            "invitation it belongs to",
        ),
        (
            "The gate passcode should be 1030 if not updated.",
            "a physical gate code -- same reason",
        ),
        (
            "Acme News Ad Account (123456789012347)",
            "the row `CREDIT_CARD` was removed for. A 15-digit number in this corpus is an "
            "account identifier the operator works with daily",
        ),
        (
            "Your order number is 12345678 and the tracking code 998877",
            "the bare word `code` is deliberately NOT a label here -- that is what makes the "
            "false-positive population the case against this recogniser predicted not materialise",
        ),
    ],
)
def test_one_time_code_leaves_these_alone(body: str, why: str) -> None:
    assert "ONE_TIME_CODE" not in redact(body).text, why


def test_one_time_code_value_has_a_four_digit_floor() -> None:
    """A first draft made the middle quantifier optional, so a bare TWO-digit number matched.

    It picked up 12 false hits on Namecheap mail in a single measured pass. No one-time code is
    two digits.
    """
    assert "ONE_TIME_CODE" not in redact("Your verification code is 42").text


# ── CREDENTIAL_ASSIGNMENT, the natural-Chinese copula — added 2026-08-31 ────────


def test_cjk_password_copula_is_removed() -> None:
    """`密码是<secret>` -- how a person writes it, against the `密码：` form a machine prints.

    Two unredacted numeric passwords reached the agent through `neo-corpus` in this shape, one of
    them the operator's own outbound. The colon form has never once matched in this corpus.
    """
    body = "验证码是绑定在了同事工位上的那台Mac上面，密码是731450"
    out = redact(body).text
    assert "CREDENTIAL_ASSIGNMENT" in out
    assert "731450" not in out


@pytest.mark.parametrize(
    ("body", "why"),
    [
        (
            "不过我上次用这个密码是2016年了",
            "'the last time I used this password was 2016' -- 1 of the 6 `密码是` rows in the "
            "corpus, and the digits after the label are A YEAR. The date guard exists for this "
            "row specifically",
        ),
        (
            "请问newsroom这台电脑主机开机密码是多少来着，我给忘了",
            "'what is the boot password again?' -- after 是 comes prose, so the value class is "
            "restricted to wholly-printable ASCII containing a digit",
        ),
        ("那就让她点忘记密码是吗？", "'should we just have her click forgot password?'"),
    ],
)
def test_cjk_password_copula_leaves_prose_alone(body: str, why: str) -> None:
    assert redact(body).text == body, why
