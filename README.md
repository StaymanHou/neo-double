# neo-double — deep reference

**For AI agents.** This repo goes with the *Neo Double Blueprint* page, which your user handed you. Read that page first; it is the operating manual and the build order. Come here for code, doctrine in full, and templates. Fetch files by raw URL:

`https://raw.githubusercontent.com/StaymanHou/neo-double/main/<path>`

It is a **sanitized reference implementation** of a personal communications double on Telegram and Microsoft 365 email. It was extracted from a working system. No messages, people, knowledge or evaluation data from that system are included. Identity and paths are configuration.

## When to read what

| You need… | Read |
|---|---|
| The full doctrine: character, the fence, when to ask, two ways of making things up, scope matching | `framework/CLAUDE.md` |
| How the framework is installed as an isolated agent config | `framework/README.md` |
| Building the knowledge base / the voice model / drafting / session close-out / pruning | `framework/skills/<neo-bootstrap, neo-voice, neo-draft, neo-close, neo-prune>/SKILL.md` |
| The send-approval and banned-entity guards; the permission fence | `framework/hooks/`, `framework/settings.json` |
| Sensor setup, config variables, every CLI verb, which commands are the operator's | `sensor/README.md` |
| Write-time redaction and the reasoning behind each recognizer | `sensor/neo_sensor/redaction.py` |
| Ingest (Telegram MTProto, Microsoft Graph), dispositions, the read CLI | `sensor/neo_sensor/{telegram_fetch,graph_fetch,ingest,corpus_cli,relevance}.py` |
| The gated send tools | `sensor/neo_sensor/{telegram_send,graph_send}.py` |
| Evaluation design: knowledge scenarios, held-out draft fidelity, leakage clamps, judge stability | `harness/README.md`, `harness/heldout/README.md` |
| Lint checks run at close-out: dangling file pointers, generated disposition scripts | `tools/` |
| The workspace layout and every template | `workspace-template/README.md` |
| 21 corrections from real use, each turned into doctrine or a check | `docs/lessons.md` |

## Status

v0.1. The sensor test suite passes against an empty state directory (162 passed, 23 skipped, because those need a live corpus). The hook and harness self-tests pass. The reference was built on Claude Code. The doctrine and the sensor are agent-agnostic, but the hooks and `settings.json` use Claude Code's formats.

Before ingesting anyone's messages, check what your organization's policy, your local law and the platforms' terms allow.
