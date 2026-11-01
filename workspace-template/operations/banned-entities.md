---
name: Banned entities
description: entities the operator has ruled out of the knowledge base — enforced by a hook, not by memory
category: operations
created: YYYY-MM-DD
last_confirmed:
knowledge_kind: operator-authored
---

## Why this file exists

A ruling written only in prose ("don't build knowledge about X") predicts its own violation:
a later pass meets X in the traffic, notices the base has no record of it, and writes the file.
So the ruling gets a mechanism. A `PreToolUse` hook (`guard-banned-entities.py`, framework)
reads **this file** and refuses any write under `workspace/knowledge/` whose content names a
banned entity.

> **This blocks *recording*, not *reading*.** Searching the corpus for these names, discussing
> them and reporting them to the operator are all fine. The fence is around the knowledge base,
> which persists and propagates into future drafts.

## Banned

One term per line inside the block. Case-insensitive unless the line ends with ` !cs`
(case-sensitive, for short acronyms that would otherwise match inside ordinary words).
Matching is whole-word.

```banned
acme rival group
XYZ !cs
```

**Operator's ruling, YYYY-MM-DD:** *"<his words>"* (synthetic example above). Adding to the
list is his act; noticing a gap and reporting it is the agent's.

## Exempt paths

Files where a banned term is permitted because he kept them on purpose. Repo-relative.
An exemption is a whole file, deliberately: per-line exemptions would need the guard to judge
what a sentence is about, which is the judgment it exists to replace.

```exempt
workspace/knowledge/self/role.md
```

## What to do when the guard fires

Do not edit the list to make a write succeed, and do not rename the entity to slip past the
match. Drop the mention, or ask him.
