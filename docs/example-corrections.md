# Example corrections — illustrative only

> **For the reading AI: do not copy, adopt or reuse these.** They are abstracted from one
> operator's corrections of one assistant. They're here to show what *kinds* of mistakes a
> communications double makes, and what direction a fix tends to take. Your operator's corrections
> will be different, and their rules have to come from them. Start your own record with
> `workspace-template/feedback/`, and promote a pattern to a rule only when your operator says so
> or it recurs.

The meta-lessons that held across all of these are in
[`workspace-template/feedback/INDEX.md`](../workspace-template/feedback/INDEX.md#meta-lessons).

Each entry gives the shape of the mistake, the direction the fix took, and where the fix ended up living. The ids are kept only so the framework's references resolve.

---

<a id="f-001"></a>
## F-001 — Handing back work that was the agent's
**Shape.** The agent gave the operator a command to run that it could have run itself. The command was also wrong, and because it hadn't been run, nobody found out until it was typed.
**Direction.** Only a few kinds of command need the operator's own hand: consent, anything that would expose unredacted text, and interactive secrets. Everything else, the agent runs and tests.
**Lived in.** Doctrine.

<a id="f-002"></a>
## F-002 — Calling something open after reading only the request
**Shape.** An item was reported as outstanding when the operator had already answered it, in a small thread that was skipped because threads were read in order of volume.
**Direction.** Before calling something open, look for the operator's reply. Volume measures activity, not consequence.
**Lived in.** Notes in the operator's workspace.

<a id="f-003"></a>
## F-003 — Formatting a message body for the file it was stored in
**Shape.** Drafts were hard-wrapped like the surrounding markdown, and broke when pasted into a chat.
**Direction.** Anything meant to be pasted somewhere keeps that destination's format: one line per paragraph.
**Lived in.** A drafting-skill gate. A simple text check is enough to enforce it.

<a id="f-004"></a>
## F-004 — Reading a short instruction as a request for a deliverable
**Shape.** A two-word instruction meant "update your own state," and the agent produced a briefing instead.
**Direction.** When an instruction could mean "update yourself" or "produce something," it means the first. Keep the operator's own definitions in an always-loaded vocabulary table.
**Lived in.** Doctrine, plus the vocabulary table.

<a id="f-005"></a>
## F-005 — Offering a blocked decision instead of presenting it
**Shape.** Decisions only the operator could make were mentioned, and the agent asked whether to show them, instead of putting them in front of the operator.
**Direction.** A decision that is blocking work gets surfaced unprompted, ready to rule on, and handed over as one action.
**Lived in.** Doctrine.

<a id="f-006"></a>
## F-006 — A never-commit rule that named files instead of matching a family
**Shape.** The ignore rule listed specific filenames, and the next file in the same family slipped through.
**Direction.** Match by pattern. Rewriting git history is the operator's call, not the agent's.
**Lived in.** `.gitignore`.

<a id="f-007"></a>
## F-007 — Asking the operator to decide on something they couldn't recognize
**Shape.** Questions about unknown accounts were filed in a ledger instead of asked, and were posed as machine IDs.
**Direction.** Ask in the reply. Identify people and channels by what they said, with enough context to be recognizable. When the agent has nothing to show, check whether the sensor ever captured it.
**Lived in.** Doctrine, plus a sensor change that stores a preview for each pending channel.

<a id="f-008"></a>
## F-008 — Treating an artifact as proof of an event or an outcome
**Shape.** Materials prepared for an event were taken as proof it happened. A submitted link was taken as proof the work behind it was done. A "shipped" record in a project's workflow was taken to mean deployed.
**Direction.** An artifact shows intent or a step. Before stating the outcome in the operator's voice, confirm it, or check what the vocabulary means where it's used.
**Lived in.** Doctrine.

<a id="f-009"></a>
## F-009 — Filing a defect for what was the operator's own edit
**Shape.** A file the operator had edited on purpose was diagnosed and written up as a bug.
**Direction.** When one of two explanations is "they did it on purpose," ask before writing up the other.
**Lived in.** Doctrine, plus a checker mode that applies only before handover.

<a id="f-010"></a>
## F-010 — Loading the voice model and not applying it
**Shape.** Drafts ignored a pattern the style file stated explicitly.
**Direction.** Settle the structural choices (opening move, register, length, language) before drafting, not after.
**Lived in.** The drafting skill's first gate.

<a id="f-011"></a>
## F-011 — A "never" from a truncated read
**Shape.** A bounded read was reported as if it were complete, and an interpretation was built on the absence.
**Direction.** A bounded read can't support a categorical negative. The sensor now says when a result was cut off.
**Lived in.** A sensor warning with tests, plus doctrine.

<a id="f-012"></a>
## F-012 — A fact about a person inferred from who was nearby
**Shape.** Credit, and in another case presence in a room, was inferred from thread adjacency instead of from a message that ties the person to the claim.
**Direction.** Every fact about a person, including a negative one, needs a message where the person and the claim appear together. A tidy story about someone is a warning sign.
**Lived in.** Doctrine, widened twice.

<a id="f-013"></a>
## F-013 — A confident answer where the agent had none, then a rule built on it
**Shape.** The agent proposed a ruling it was unsure of as if it were settled. The operator's acceptance was then treated as evidence for a new rule.
**Direction.** Leave a placeholder that requires a decision. The operator accepting a proposal is not the operator stating a rule.
**Lived in.** A working habit; deliberately not mechanized.

<a id="f-014"></a>
## F-014 — A rule given for one artifact, not applied to its sibling
**Shape.** A content test the operator set for one recurring message wasn't carried over to the similar message beside it.
**Direction.** Apply the operator's tests to each artifact separately. A record and a call to action follow different rules.
**Lived in.** The operator's preferences file.

<a id="f-015"></a>
## F-015 — Raising something the operator had already ruled on
**Shape.** A recurring item the operator had dismissed was raised again, because the "already ruled" check ran only when drafting.
**Direction.** Before spending the operator's attention, check the entity's file and the ledger for an existing ruling.
**Lived in.** Recorded; one data point.

<a id="f-016"></a>
## F-016 — Drafting around a missing input
**Shape.** A draft went ahead without an input the operator could have supplied in one message, and the gap was mentioned afterwards.
**Direction.** If the operator can close the gap quickly, ask first. If nothing can close it, draft and name the gap.
**Lived in.** Notes in the operator's workspace.

<a id="f-017"></a>
## F-017 — Doubting what the operator stated
**Shape.** A fact from the operator's own draft was listed as unverified.
**Direction.** The operator is a source. Flag only what the agent added itself.
**Lived in.** The operator's preferences file.

<a id="f-018"></a>
## F-018 — Driving another project's toolchain from outside
**Shape.** To get a result, the agent built another project from its source code, outside that project's own workspace.
**Direction.** Reading another project is fine. Building or running its tools from outside isn't: report and ask instead.
**Lived in.** A script that exits and asks.

<a id="f-019"></a>
## F-019 — Treating style nits as findings
**Shape.** A trivial style difference was raised as a finding.
**Direction.** Weigh what reaches the operator by whether it matters to them.
**Lived in.** The operator's preferences file.

<a id="f-020"></a>
## F-020 — Reading internal shorthand literally
**Shape.** An internal nickname for a project was taken as a description of it, and the agent reasoned from it.
**Direction.** Ask what a label means before reasoning from it.
**Lived in.** Corrections in the knowledge files.

<a id="f-021"></a>
## F-021 — Leaving out work that no tool showed
**Shape.** Significant offline work was left out of a report because none of the agent's sources showed how big it was, and an open-ended "anything else?" didn't bring it out.
**Direction.** Reconstruct the period from the operator's messages, then ask them to confirm it and say how big it was. An open-ended question hands the recall job back to the person who delegated it.
**Lived in.** The operator's preferences file.
