# Framework layer

This directory is Neo's **framework**: persona, doctrine, skills, guard hooks and the permission
fence. It installs as an isolated Claude Code config directory, `~/.config/claude-neo/`.

Everything about *one particular operator's world* — who their colleagues are, how they write, which
chats are work, what they have corrected — lives in a separate **workspace repo**
(`~/neo-workspace/`), never here.

## The split, and the one test that decides it

> *Would this still be true for a different operator at a different company?*
> **Yes** → framework (this directory, installed to `~/.config/claude-neo/`).
> **No** → workspace (`~/neo-workspace/workspace/`).

Getting it wrong is expensive in both directions: doctrine written into the workspace is invisible
to a fresh session, and an operator's world written into the framework is wrong for the next
operator and leaks into every evaluation fixture.

## Install

```sh
mkdir -p ~/.config/claude-neo
cp -R CLAUDE.md settings.json statusline.sh hooks skills ~/.config/claude-neo/
sed -i '' "s#<HOME>#$HOME#g" ~/.config/claude-neo/settings.json   # GNU sed: drop the ''
chmod +x ~/.config/claude-neo/statusline.sh
cd ~/.config/claude-neo && git init   # local-only; see neo-close Gate 7 before adding a remote
```

Then fill in **§ Who the operator is** in `~/.config/claude-neo/CLAUDE.md`, and configure the
footnote constant in the sensor's send modules.

Run sessions from the workspace repo with the isolated config:

```sh
alias claude-neo='CLAUDE_CONFIG_DIR=$HOME/.config/claude-neo claude'
cd ~/neo-workspace && claude-neo
```

The links in these files to `docs/example-corrections.md` resolve inside this repo, not after install. They point
at the anonymized failure record each rule came from.

## What is here

| Path | What it does |
|---|---|
| `CLAUDE.md` | The doctrine: character, the two ways of making things up, the fence, which commands are the operator's and which are yours, scope matching, when to ask, show-the-input-before-you-spend, the corpus traps |
| `settings.json` | The permission fence. `neo-corpus` allowlisted; the raw store, `sqlite3`/`psql`/`docker` and `neo-channels` **denied**. Denied rather than unlisted because sessions run in auto mode, where unlisted commands are auto-approved |
| `statusline.sh` | Model · context usage % · session spend. Needs `jq` |
| `hooks/guard-send-approval.py` | `PreToolUse` on Bash. Refuses `neo-send`/`neo-mail --approve` unless the `--quote` is verbatim in the operator's most recent message; refuses approve+commit in one command. Fail-closed. `--self-test` |
| `hooks/guard-banned-entities.py` | `PreToolUse` on writes. Refuses any write into `workspace/knowledge/**` that names an entity on the operator's ban list (`workspace/operations/banned-entities.md`, found by walking up from the target file). Blocks recording, not reading. Fail-closed. `--self-test` |
| `skills/neo-bootstrap` | First build of the knowledge base from the corpus: sourced claims only, outside-in (orgs → projects → people), dispositions proposed as a script the operator runs |
| `skills/neo-voice` | Builds `style.md` (how they write) and `values.md` (how they decide) from their own outbound, behind a held-out clamp so the evaluation stays honest |
| `skills/neo-draft` | The point of the project: drafts in their voice that carry their decision unchanged. Envelope first, then content; the gated send |
| `skills/neo-close` | End-of-session audit: what was learned but not written, unknown entities, stale numbers, dead pointers, bookkeeping, commit |
| `skills/neo-prune` | Compacts the workspace `CLAUDE.md` by moving dated narrative to `workspace/history/` verbatim |

Both hooks run their self-tests from any directory: `python3 hooks/<name>.py --self-test`.
