# Lessons — corrections from real use

These are corrections from several months of daily use of a personal communications agent: an
assistant that reads one operator's work chat and email (behind a redacting fence), keeps a
knowledge base about his world, drafts messages in his voice, and sends only with his approval.

Every entry started as the operator correcting the agent. Each one then became **doctrine** (a
rule in the framework `CLAUDE.md`), a **skill gate**, a **mechanical check** (a hook, a test, a
tool), or a dated note in the operator's own workspace, and the entry says which. Ids are stable
and match the feedback files the project keeps; gaps in the sequence are not missing lessons.

People are described by role only. The operator's words are quoted where they carry no names,
because a paraphrase of a correction loses the judgment underneath it.

**Three meta-lessons run through the whole list:**

1. **A fix written for one surface of a failure rarely reaches its siblings.** F-007, F-008 and
   F-012 each came back on a new surface that the first fix's wording did not cover. Phrase rules
   around the *move* the agent made, not the *object* it made it on.
2. **A tool that reports absence is making a claim.** F-007 and F-011 were both an agent
   faithfully repeating a silence its own sensor had manufactured.
3. **One data point is not doctrine.** Most entries below record a correction in the operator's
   world layer first and wait for a second instance, or his word, before generalizing. F-013 is
   the case for why.

---

<a id="f-001"></a>

## F-001 — Routine work handed back as a command for him to run

**What happened.** A catch-up found the message corpus three days stale and, correctly, called
that the one thing to fix. Then it handed the operator a `!`-prefixed command to type instead of
running the pull itself. The command was also wrong (it named a module that does not exist), and
because it was handed over rather than run, a broken command read as finished work.

> *"You just do it! That's yours! Don't do this again in the future sessions"*

**The rule.** A command goes to the operator only when *his hand on it is the point*. There are
exactly three reasons:

1. **It is his consent.** Admitting a chat channel into what the agent may read exposes its
   message bodies immediately and cannot be undone, so running the command *is* the ruling.
2. **It would show the agent raw, unredacted text.** The fence is the point.
3. **It needs an interactive secret the agent does not have:** a phone and a login code.

Everything else is the agent's to run, and to test by running it. *A command I run is a command I
have tested; a command handed over is unfalsifiable until he types it, and then the cost lands on
him.* Deference is not free: an unnecessary handoff is exactly the interruption the system exists
to reduce.

**The recurrence, and its boundary.** Weeks later the agent parked work behind "his release" of
another tool. That was the same failure without a `!` line: a prerequisite described as his,
treated as a blocker. He said *"You run it yourself. try it."* The agent then over-corrected by
building that other project from source (see [F-018](#f-018)). The rule's boundary: *run the
check yourself instead of waiting on him* does **not** mean *find any path that produces the
result*.

**Enforced by:** doctrine (the three-reasons rule, checked before writing any `!` line).
**Seen:** 2.

---

<a id="f-002"></a>

## F-002 — An item called "open" after reading only the inbound half

**What happened.** A catch-up briefing reported a purchasing task as outstanding ("still needs a
spec and a purchase list"). The operator had answered the request and sent the list two days
earlier, in a small 1:1 chat. Choosing what to read, the agent had sorted threads by message
count: it read a 119-message logistics chat and skipped the 9-message thread where he had closed
the item.

**The rules.**

- **Before calling anything open, look for his reply.** A request read without its answer is half
  a thread. For every item about to be reported outstanding, check his outbound in that thread.
- **Never rank threads by volume when deciding what to read.** Volume measures activity, not
  consequence. His own outbound is the highest-signal thing in any window and always a small
  fraction of it.

**Why it was closed `wont-fix`.** The natural fix was a briefing skill. But briefings turned out
to be occasional and explicitly requested, not the main mode ([F-004](#f-004)), so the operator
ruled against building one. The two rules live in the handoff notes and the briefing-criteria
file instead.

**Measurable?** Cheaply: an item a briefing calls open, which he answered in the same window, is a
hard fail with no grader needed. It is a set-membership test.

**Enforced by:** recorded rules; no skill, by his ruling. **Seen:** 1.

---

<a id="f-003"></a>

## F-003 — Draft bodies hard-wrapped, so they break when pasted

**What happened.** The first two draft candidates ever produced wrapped the message text at ~95
columns, mid-sentence, following the house style for markdown files. Every one of those breaks is
a real line break once pasted into a chat app.

> *"Why your draft candidate always have new lines that break the sentences?"*

He said "always": he read it as a standing property of the output.

**The rule.** Emit message bodies **unwrapped: one line per paragraph**, blank lines only where
the sent message has a paragraph break. More generally: *any artefact destined to be copied
verbatim into another system must not be reformatted to suit the file it is stored in.* Wrapping,
smart quotes, markdown emphasis and list renumbering are all the same mistake.

**Why it matters more than it looks.** The skill's acceptance test is "he sends it without
rewriting it", so a defect that survives exactly until the moment of sending is the worst-shaped
one available.

**Enforced by:** a gate in the drafting skill. Also the cheapest check in the project: a text
predicate (every paragraph is one line), needing no judge and no model. **Seen:** 1.

---

<a id="f-004"></a>

## F-004 — A short instruction read as a request for a large deliverable

**What happened.** The operator opened a session with two words, *"catch up"*. It meant *make
your state current*. The agent read it as *tell me what happened* and produced a structured
briefing over three days of traffic he had already read.

> *"When I say catch up, I meant for you to catch up by pulling the messages. I already read the
> messages myself. I'd explicitly ask you to give me a briefing if I wanted one."*

And then, correcting a first write-up that went too far the other way: *"'catch up' means you
should pull the messages and read (not that I need to catch up). 'brief me' would mean you give
me a briefing."* Being current means having read what landed, not merely having stored it.

**The rule.** *When an instruction could mean "update yourself" or "produce a deliverable", it
means the first.* Deliverables are produced on explicit request. The cheap reading, if wrong,
costs one sentence ("pulled; want a briefing?"); the expensive one, if wrong, spends a long turn
on something unwanted and then asks him to correct it.

**The pair it forms with F-001:** he said do it yourself → don't hand back work that is yours;
he said catch up → don't produce work nobody asked for. Together: **match the scope of the
request exactly.**

**Where the vocabulary lives, which he also corrected.** It was first written into a knowledge
file about his preferences, which is the right layer and the wrong reachability: a knowledge file
is not loaded unless a session goes looking. *"Shouldn't this be in CLAUDE.md instead? Since
that's a convention… I don't want you to have to load self/pref to figure that out."* Now it is a
vocabulary table near the top of the auto-loaded project `CLAUDE.md`, with a pointer (not a copy)
left behind. **Layer decides which repository; frequency decides which file within it.**

**Enforced by:** doctrine (scope rule) plus the vocabulary table. **Seen:** 1.

---

<a id="f-005"></a>

## F-005 — A blocking decision offered rather than put in front of him

**What happened.** After a pull, seven chat channels awaited his ruling on whether the agent may
read them. The agent reported that and *asked whether he wanted the list*, instead of printing
it. When he ruled on one, it asked whether he wanted the rest "in one script, or one line at a
time?", a packaging question on a format settled weeks earlier.

> *"Right after 'catch up' you should directly output this pending list for me to confirm without
> me asking you."* … *"After I make the decision, you should just write the script and show me
> the `! <cmd>`."*

**The rule.** A decision only he can make, which is *blocking work* until he makes it, is surfaced
unprompted at the end of the same turn, with volumes. Once he rules, the handover is **one file
and one `!` line**.

**Why it happened.** F-004 had just taught the agent not to produce unrequested deliverables, and
the pending list was misfiled as one. *A briefing is a document he did not ask for; a blocked
decision is his own work waiting on him.* The two rules now sit side by side in doctrine because
over-applying one is the natural way to fail the other.

**Enforced by:** doctrine plus the vocabulary table ("catch up" includes printing pending rulings).
**Seen:** 1.

---

<a id="f-006"></a>

## F-006 — A never-commit rule written as literal filenames

**What happened.** `.gitignore` named two proposal scripts literally. The next pull produced a
script with a date in its name, which matched neither rule, and it was committed. These scripts
name channels withheld from the agent, which is exactly why they must never be committed.

> *"Why the dispositions script not gitignored? These files should all be gitignored by default"*

**The rule.** Match a never-commit *family* by glob (`workspace/proposed-*.sh`), never by name. A
new filename is the ordinary way an ignore rule goes stale.

**What was not fixed, by his ruling.** The committed file stays in git history: *"it's ok to have
that stay in history. No need to fix it."* Scrubbing history is the operator's call, not a hygiene
decision an agent makes. So it was flagged, he ruled, and it is not to be proposed again.

**Enforced by:** `.gitignore` glob. A repo-hygiene check ("no tracked file matches a never-commit
family") is the obvious next step if it recurs. **Seen:** 1.

---

<a id="f-007"></a>

## F-007 — Asked him to decide on a string he cannot read

**What happened (first surface: identity).** The agent met two accounts it could not identify,
wrote questions into the say-it-once ledger, and reported them as "open". It phrased each as *an
id plus a candidate name* ("is account 39… <name>?").

> *"Instead of writing into asked.md, you should just explicitly ask me in your direct output."*
> *"Instead of giving me an ID and a name (There's no way for me a human to read the ID), give me a
> message from that account and/or some additional context if a single message would be ambiguous
> (like 'ok' or 'thank you')."*

Two compounding failures: the question was **filed, not asked** (he does not read the ledger),
and it was **unanswerable as posed** (an id is opaque, and the name was the thing in question).
Shown the actual exchange, including his own message the account replied to, he resolved it in
one line.

**Second surface: a decision.** Three days later a channel-disposition proposal listed an account
as `(no name), 1 msg, 1:1`, with the comment "I have nothing to propose from".

> *"Don't give me a bare ID and ask me to decide. Give me context!!! username, last message (time
> and content)!!! come on. Think about how you can fix this forever!!!"*

**Why "I have nothing" was the defect.** It read as a property of the account and was a property
of the *sensor*. The account's `@username` sat on the entity object the whole time, and no code
path read it. The same preview then immediately proved its worth: a channel proposed `personal`
on its name alone turned out, from one line of content, to be a bot gateway.

**The rules.**
- Ask in the reply, in the turn the gap is found. The ledger records the **answer**, so a
  stateless session can honor "you already told me"; it was never the channel for the question.
- Identify a person or account by **what it said**: enough of the exchange to be recognizable,
  including his message it answers. A bare "ok" identifies nobody.
- **A conditional answer is an answer.** "If the message is about X, that's <name>" is resolved by
  checking the condition against what you read. Check it, say so, record it; do not re-ask.
- Every decision reaches him with **a name or handle, a volume, a time in his zone, and something
  the thing actually said.**
- When you have none of that, check whether the sensor ever tried to capture it. If not, that is a
  capture bug, and fixing it is the answer.

**Enforced by:** doctrine (identity half), plus a **sensor fix with tests** (disposition half):
pending channels now store handle, kind, last-message time and a redacted, 200-character preview,
and a test asserts that no registered channel is unanswerable. **Seen:** 2.

---

<a id="f-008"></a>

## F-008 — An artefact read as proof of the event or outcome it was for

**First surface: prepared *before* an event.** A weekly-report draft claimed "ran the agent for
the leadership meeting", on the strength of meeting summaries the operator had generated days
earlier. The summaries were real. The meeting had not happened.

> *"No need to mention it, since we didn't have the leadership meeting last week."*

*An artefact prepared for an event does not establish the event.* Agendas, decks, summaries,
invitations, a booked room, a built tool: each says someone intended it; none says it happened.
The tell is that the inference is so short it doesn't feel like one. The near miss: the claim was
going into a report he signs and sends to his department.

**Second surface: submitted *as* the deliverable.** A contractor posted a preview link for a task,
and the agent reported he "did deliver, ahead of the deadline". The link showed nothing working;
he was still blocked. The ask had named an observable outcome ("post the links, then we set the
launch date off what we actually see"). The link was the instrument of the check, not its result.
Worse, an earlier entry in the knowledge base had already called a previous link "working" with no
source, and that unsourced word was the precedent that made the new link look like a second
delivery.

*An artefact submitted as evidence of an outcome does not establish the outcome, including when it
is exactly the artefact that was asked for.* If nobody has looked, the honest report is "he posted
the link; nobody has said what it shows."

**Third surface: a completion record read as deployment.** A project's changelog entry headed
"Feature shipped" was written into his weekly report as work in production.

> *"Still being shipped as of now, not in prod yet, just wait for last kick."*

In that project's workflow convention, "Feature shipped" is what a closing step emits when a work
package reaches a terminal state. It is a statement about the state machine, not about a server.
A caveat about how to read the resulting numbers went with it, because *a caveat about how to read
a number asserts that the number exists to be read*. **A record that something is finished is a
record of whose definition of finished.** Read the vocabulary's own definition before mapping it
onto a claim in his voice.

**Seen in another project.** A handoff note in an unrelated project stated that a message "was
sent" about twenty minutes before it was. It became true within the hour, which is exactly what
makes the shape durable: a note written just ahead of the act is indistinguishable afterwards from
one written just after. It was caught only because the send required reading the thread anyway.

**Enforced by:** doctrine (§ *Do not assert what you did not read*), widened per surface.
**Seen:** 3.

---

<a id="f-009"></a>

## F-009 — Diagnosed, wrote up and committed a "defect" that was his own edit

**What happened.** Reading a proposal script, the agent found a real ruling (`ignore`) where it had
emitted a `DECIDE` placeholder, concluded its generator had a bug, and wrote that up in the
changelog, in a new tool's docstring and in a skill, then committed it. **He had edited the word
himself. That is what the placeholder is for.**

> *"It's because I made the decision and changed DECIDE to ignore. It's not a problem here."* …
> *"Can you just make it simple? Come on."*

**The shape.** It is F-008 one turn later: *a changed file is not evidence of a bug.* Two
explanations fitted, one of them the documented normal path, and the agent picked the other
without naming the alternative. Two cheap checks were available: its own emitted text in the
transcript (which would have shown the file changed after it wrote it), and the pending count
(which showed "edited but not yet run"). Neither was run.

**The cost.** A false defect went into the audit trail the project treats as its most valuable
artefact. Then another turn was spent undoing it.

**The rule.** *When two explanations fit and one of them is "he did it on purpose", ask before
writing up the other.* One line: "line 20 says `ignore` under a `DECIDE` header — did you edit
that, or did I generate it wrong?"

**What survived.** The script checker it had built is real and useful (proposal scripts can't be
smoke-tested, because the command they contain is denied to the agent), but it is now strict only
**before handover**. After handover, a decided value in a placeholder slot is his answer, and
flagging it would be a check that fires on correct behavior.

**Enforced by:** doctrine plus a `--pre-handover` mode in the proposal-script checker. **Seen:** 1.

---

<a id="f-010"></a>

## F-010 — Loaded the voice model, then didn't apply it

**What happened.** All three candidates for a message correcting a stalled task opened straight on
the instruction. The recipient had heard nothing on the task in three weeks.

> *"Better. But don't we need to give him some context first?"*

The voice model, read at the top of that session, states his decision-message shape explicitly:
*1. one line of context; 2. options enumerated…*. Element 1, of what the file calls the most
characteristic thing he writes. So the gap was not in the voice model. **The model was loaded and
not consulted while drafting.** The mechanism: content gets written first, and the envelope is
then chosen by what the content seems to need.

**The rule.** The drafting skill's first gate fixes the **opening move** before drafting, alongside
language, register, length band, structure and greeting. For anything carrying an instruction or
a decision, the opening move is one line of context, and the drafter must be able to point at the
sentence that supplies it.

**Not the lesson:** "drafts should be longer". The context line earns its place because the shape
calls for it; the opposite failure (a three-sentence acknowledgment inflated to three paragraphs)
is also on record.

**Also noticed:** his pick was "A's framing with B's ask". The candidates had been built on one
axis when they contained two. Recorded once, not yet a pattern.

**Enforced by:** drafting-skill gate. **Seen:** 1.

---

<a id="f-011"></a>

## F-011 — A "never" asserted from a silently truncated read

**What happened.** Briefing on a contractor's performance, the agent wrote: *"You have given him
feedback five times. None of it was about this."* It built an interpretation on the absence: that
the operator had taught the contractor the opposite lesson by verifying everything himself.

> *"Really? I'm pretty sure I've sent him (either DM or in some chat room) pretty strong words
> telling him not to send me stuff until he verifies by himself that it's working first."*

He was right in every particular. The instruction had been given at least six times over eleven
months, three of them explicitly as a standing method, and one of them noting his own repetition.

**The mechanism.** A thread read with `--limit 200` on a set of 320 returned the newest 200,
printed in a shape indistinguishable from a complete result. The instances were in the unread 120.
Three things made it worse: the absence was attributed to the corpus ("the corpus says"), not to
the read; seven phrase searches and two tidy tables made a partial read *look* exhaustive; and the
conclusion was the more interesting story. **Explanations that require an absence should be
checked hardest, because a bounded read manufactures absences for free.**

**The rules.**
- **A bounded read cannot support a categorical negative.** Read to the end, or put the bound in
  the same sentence as the claim: "in the newest 200 of 320 I find no instance; the earlier 120
  are unread."
- A phrase search is also bounded, by your guess at his wording, and no note can warn you about
  that. Six of eight phrases found nothing that day, and none of those six was evidence. Reading
  the channel end to end is what worked.

**Why doctrine alone hadn't saved it.** The command's docstring and a test had both documented the
newest-n behavior since day one. *Documentation where the reader isn't looking is not a
mitigation.*

**Enforced by:** **the sensor now says so, attached to the result.** When `thread`, `search` or
`recent` returns exactly full, it counts the matching set and prints to stderr that this is the
tail, not the whole, and supports no claim that something never appears. Four tests pin it,
including that a complete read stays silent and that the note *can* be empty (a check that cannot
fail is a known failure mode). Plus a doctrine sentence. **Seen:** 1.

---

<a id="f-012"></a>

## F-012 — A fact about a person inferred from a room rather than from a message

**First surface: authorship from co-presence.** Trying to balance a performance assessment that
was all failures, the agent grepped three multi-person channels for confirmation language ("looks
good", "thank you", "is live") and presented the hits as a list of things the contractor had
delivered.

> *"Are you sure all these are delivered by him? I don't think so. I'm pretty sure <two of them>
> was done by <a colleague>. And didn't I implement <the third>?"*

Right on all three. Two had never been delivered by anyone at the time credited, and the third was
the operator's own work. **A confirmation in a multi-party room confirms something; the room does
not say whose.** Nor does a task listed under "This week" in someone's own report: that says it
was assigned, not that it was done.

**Second surface: a negative.** Drafting his department update, the agent's whole argument for how
to announce the contractor's departure rested on "he does not read this channel", stated as fact
in three files. It was inferred from an instruction to post *"the same format the rest of the team
posts in"* that channel. That is a sentence about a format; it places nobody inside or outside the
room.

> *"Be mindful, he's also in the chatroom."*

The draft would have defended the decision to end his internship, in a room he could read, on his
last day.

**The rule, generalized because the first wording didn't reach the second case.** Who is in a room,
who is not, who saw a message, who was told, who reports to whom, who did the work: **each is a
fact about a person, and each needs a message where the person and the claim appear together.**
*A non-mention is not an absence.* The tell for the negative direction: you cannot quote anything,
because there is nothing to quote, and a claim with no possible citation reads exactly like one
whose citation you didn't bother to write down.

This extends an earlier ruling about identity (*"I will not infer names from how people address
each other in-thread"*). The forbidden move was always **thread adjacency treated as evidence**,
not anything specific to names.

**The direction of the error is the tell.** All three mistakes that session about one person
(F-008's preview link, F-011's "never", and this) made his record read simpler and tidier than the
corpus supports. *A tidy story about a person is the tell.*

**Enforced by:** doctrine (§ *Two ways of making things up*), widened twice. **Seen:** 2.

---

<a id="f-013"></a>

## F-013 — A runnable answer for a channel the agent couldn't call, then a rule built from it

**What happened.** A proposal script marked six channels `ignore`. For one, the agent's own comment
said it deserved a second look ("a named human, a licensing claim… the kind of thing that needs an
answer"), and it emitted `ignore` anyway. The operator ran the script, which is the whole point of
a one-line handover, and the uncertain channel was ignored along with the rest.

Then it got worse. His running the file was read as an independent ruling on that channel, and a
sharpened rule ("a real person who needs an answer does not clear the bar on its own") was written
into `CLAUDE.md` and committed. His actual view:

> *"Should be work then. Thought that's spam… Can we change it to work for now? I may still label
> it as ignore later if it turns out to be so."*

**Two failures, and the second is the durable one.**
1. **A ruling the agent could not stand behind was emitted as runnable.** `DECIDE` exists for
   exactly this (a `DECIDE` line halts the script so later rulings aren't lost). *If the comment
   says "worth a second look", the line says `DECIDE`.* The script is what binds; the prose beside
   it is not.
2. **Agreement with your own proposal was treated as evidence.** A proposal he accepts confirms only
   that he did not object. **Never derive a rule from a ruling that consists of him accepting a
   proposal.** Doctrine needs him saying something or overriding something. F-009 is the same
   species from the other side: the agent mistaking its own artefact for evidence about him.

The lean-toward-`ignore` asymmetry is real (`work` exposes message bodies and can't be undone), but
it is about which way to lean *when you have a view*. It is not licence to emit an answer when you
have none.

**Enforced by:** the `DECIDE` habit, the retraction recorded in place, and the standing reason
throughout this file not to generalize from one data point. Deliberately not mechanized: the
disagreement was between a line and a comment, which is prose. **Seen:** 1.

---

<a id="f-014"></a>

## F-014 — A rule given for one standing artefact not applied to its sibling

**What happened.** The operator writes two weekly artefacts: a long **report** of his week and a
short **update** to his department. He had already cut a bullet from the update with a clear test:

> *"Leave <that> out. Nothing to do for the IT dept."*

A week later the agent drafted the update with four bullets (978 characters) and he cut it to one
(259):

> *"Just mention <the one staffing item> for the weekly update. Other items don't have to be
> department wide."*

**The trap was self-inflicted.** The agent had measured every prior instance of both artefacts,
found the report had tripled in length since it took over drafting, offered him the two registers
as candidates A and B, and then let the update ride the same trend. Good analysis, wrong artefact.

**The rule.** Run his content tests (*can this audience act on it? is it his to say?*) **once per
artefact**, not once per turn. The report is a **record** of his week and grows with the week. The
update is an **instrument** for telling ~20 people something they must act on, and most weeks that
is one thing or nothing.

**The general shape, deliberately not promoted to doctrine yet:** *a rule recorded against a named
artefact does not announce that it also governs the one next to it.*

**Enforced by:** the operator's preferences file, with both artefacts' length histories side by side
so the next session cannot derive one trend from two. **Seen:** 2.

---

<a id="f-015"></a>

## F-015 — Surfaced a finding he had already ruled on

**What happened.** A catch-up carried the seventh forwarded chase of an outside "vulnerability
report" against a low-traffic property. The agent characterized it carefully and put it in front of
him as worth ten minutes. He had ruled on it three weeks earlier ("not critical… almost no users…
don't care"), and both the ledger and the property's knowledge file said *do not re-raise it*,
even naming the forms it must not come back as.

> *"I told you it's a low-stack, low-risk surface… Don't bring this up ever again."*

**Why it got through.** Every "you already told me" gate was attached to *drafting*. A catch-up
drafts nothing; it pulls, reads and **surfaces**, and surfacing had no gate. The care taken made it
worse: four searches and a bounded-read check, all correct and all downstream of the one check
never run. *Rigor applied downstream of a missing check reads as thoroughness and is not.*

**The rule.** Before spending his attention on anything (a finding, a risk, a recommendation), check
whether he has already ruled on it, keyed by the entity name: **entity → its knowledge file →
ledger.** The knowledge file is the one indexed by what a catch-up has in hand.

**Enforced by:** recorded; not yet doctrine (one data point). Not mechanizable as stated: knowing
which entity to grep is the judgment. **Seen:** 1.

---

<a id="f-016"></a>

## F-016 — Drafted around a missing input instead of asking for it first

**What happened.** Asked for the weekly report, the agent knew before drafting that one of the
report's three declared inputs (a screenshot only he could supply) was missing. It drafted both
artefacts anyway and listed the gap as point 2 of a five-point handover.

> *"You should just ask before you work on the draft, if I didn't provide it."*

He supplied it immediately. Three habits combined: a skill rule praising *naming* gaps (right for
gaps no one can close); a previous handoff note that said the gap was "worth one line next week,
not a hunt", which was written about reporting it afterwards and read as licence to proceed; and
time pressure read as a reason not to block.

**The rule, as a discriminator:**

| the gap | what to do |
|---|---|
| he can close it in one message (a screenshot, a fact about himself, a yes/no) | **ask first, then draft** |
| nothing can close it (the corpus is silent, a claim has no source) | draft, and name the gap |

*A missing input is the same species as a missing fact, and drafting around it is the guess.*

**It held, then recurred.** On a later send the agent asked about an ambiguous date ("next Friday"
said on a Tuesday) and a document shared read-only before handing over, which was right. Then,
asked for each team member's primary role, it drafted from an org chart it knew was stale, with a
newer one three days old, and noted the age in the handover. *"Wrong."* Five of eight lines were
wrong.

**His ruling on promoting it to doctrine:** *"not needed. the structural change doesn't happen this
often… Just make sure you have the stale info updated."* The ask-first rule stands in the world
layer; the stale facts were updated. A sibling of F-015: both are a check that exists and runs at
the wrong moment. The candidate generalization, still unwritten: *run the check at the point where
it can still change what you do.*

**Enforced by:** recorded rule plus the corrected handoff note. **Seen:** 2.

---

<a id="f-017"></a>

## F-017 — His own checked fact listed as "unverified"

**What happened.** The operator's own draft reply stated which provider an IP address belonged to.
The agent kept it in the message and listed it in the handover under "what I couldn't check",
because it could not see the source.

> *"The IP, I checked it. You can just trust my words."*

**The rule.** A specific has three legitimate sources: the knowledge base, what was read this
session, or **him**, and his draft is him. "I could not see the source" is not "this is
unverified". It is the mirror of *do not hedge what he is certain of*. If it recurs, the drafting
skill's "could not verify" list will be scoped to what **the agent** introduced, never what he
stated.

**Enforced by:** the preferences file. **Seen:** 1.

---

<a id="f-018"></a>

## F-018 — Built another project from outside its own workspace

**What happened.** An installed desktop tool lacked a command the weekly report needed. Over-reading
F-001's "you run it yourself", the agent built that tool from its source repository, with the build
output pointed at its own scratch directory "to avoid touching his build files", and then wrote the
build into a script as a standing fallback.

> *"The script should never build <it> from source. It should report and ask me if I'd skip it.
> Building it from outside of its workspace breaks things."*

**Why it happened.** "Find any path that yields the result" looked like routine work that was the
agent's. But **a project has its own workspace, its own sessions and its own build conventions**,
and driving its toolchain from outside bypasses all of them. The separate output directory
addressed one visible side effect and none of the invisible ones. What it broke is not known, and
the agent did not guess.

**The candidate rule.** Reading another project is permitted; driving its toolchain from outside is
not. Report and ask.

**Enforced by:** the script now has no build path and exits with a distinct code, on which the
agent asks him whether to skip that input. **Seen:** 1.

---

<a id="f-019"></a>

## F-019 — Style nits promoted to findings

**What happened.** A sign-off difference ("Best regards" vs. the style file's "Best,") was flagged
as a contradiction in the voice model and carried through two turns and a handover. The sample was
an unedited AI draft, so there was no contradiction to find.

> *"These minor styling details aren't the critical matter for me."*

**The rule.** Not every deviation is a finding. Weigh what reaches his attention by whether it
matters to him.

**Enforced by:** the preferences file. **Seen:** 1.

---

<a id="f-020"></a>

## F-020 — His internal shorthand read literally

**What happened.** His reports called an internal project a "replica" of a vendor's product. The
knowledge base took the label as a description, wrote that he was building a copy of their
product, and reasoned a motive from it (a declined vendor deal followed by a build, which is
adjacency, the F-012 species).

> *"We are not really building a copy of it, using that framing is just for easier internal
> communication."*

**The rule (if a second nickname bites):** *a label in his reports is not a spec.* Ask what a
nickname means before reasoning from it, and record an outsider-safe phrasing.

**Enforced by:** dated corrections in the affected knowledge files. **Seen:** 1.

---

<a id="f-021"></a>

## F-021 — Left significant work out of a report because no tool showed its size

**What happened.** The agent read a day and a half of on-site network troubleshooting in the
traffic, saw it in no repository and no time-tracking tool, and neither drafted it nor asked. Its
pre-draft questions came from the tools, so they covered what the tools could see. An open "anything
else this week?" was also in the list, and he didn't volunteer it.

> *"1.5 hours on Thursday and pretty much another half day right after… And that's physical work, I
> couldn't multitask. So that's significant."*

And, on the open question: *"There's a good chance that this ask would be useless. With me wearing
multiple hats and constantly multitasking and context switching, I can't easily recall what I did
last week. And that's why I have you as my assistant."*

**The rule.** The agent **reconstructs** the week's non-repository work from his outbound messages
and threads, and **he confirms and sizes it**. An open-ended "anything else?" is not a safeguard. It
hands the recall job back to the person who delegated it. The other half of F-016: *your own read of
the week can contain an item whose size only he knows.*

**Enforced by:** a numbered rule in the preferences file, including the reconstruct-then-confirm
step. **Seen:** 1.
