---
name: deepseek
description: Delegates tasks to the DeepSeek API (cheap models) so they don't spend Claude usage or fill this context window. Use when the user says "use o deepseek", "manda pro deepseek", "deepseek pra ...", "pede pro deepseek", "dispara subagentes deepseek", or asks to delegate, offload or parallelize reading, analysis, summarization, code generation or review to DeepSeek. Also covers checking DeepSeek balance and spend.
---

# DeepSeek delegation

Script: `python3 ~/.claude/skills/deepseek/ds.py` (stdlib only). The full answer goes to a file. Stdout shows a 20-line preview, the file path and a usage line (cache-hit %, cost in USD). Read the file only if the preview isn't enough, and read only the part you need.

## Modes

| Need | Command |
|---|---|
| Quick question or generation | `ds.py "task"` |
| Over specific files | `ds.py -f a.py -f b.py "task"` |
| Follow-ups on the same material | `ds.py -s NAME -f ... "task"`, then `ds.py -s NAME "next question"` (no `-f` again) |
| DeepSeek explores a repo itself (read-only list/read/grep) | `ds.py --agent /path/to/repo "task"` (uses **pro** by default; add `--flash` for simple lookups) |
| Harder reasoning outside agent mode | add `--pro` (about 4x the price of flash; flash is the default for ask/session) |
| Long task text | `ds.py - <<'EOF' ... EOF` |
| Balance / spend | `ds.py --balance`, `ds.py --stats [DAYS]` |

## Context protection (subagents)

- Use `--agent` instead of reading many files yourself. DeepSeek does the reading, and you get the conclusion.
- For independent pieces of work, start several runs with Bash `run_in_background: true`, each with its own `-o ~/.cache/deepseek/out/<name>.md`. Don't wrap them in Claude Agent spawns: that spends the Claude usage this skill exists to save.
- After background runs finish, read only the `[ds]` lines and previews. Open the output files only when needed.

## Cache-hit rules (input is ~50x cheaper on a hit)

1. **Same material, many questions → session.** Attach the files once with `-s`. Each follow-up resends the identical prefix and hits the cache.
2. **Fan-out over the same files → warm first.** Run ONE call with the shared `-f` files, wait for it to finish, then start the parallel calls with the same `-f` set in the same order. The script sorts files, so the set just has to be identical. Parallel calls started cold all miss.
3. **Shared files first, varying question last.** The script already does this layout, so don't put file contents into the task text.
4. **Keep the model consistent.** The flash and pro caches are separate. A session remembers its model.
5. Never edit the `SYSTEM`/`AGENT_SYSTEM` strings in `ds.py` casually: any change invalidates every cached prefix.

## Delegation guidance

- Good fits: summarizing large files or logs, exploring a codebase, drafting boilerplate or tests, first-pass reviews, translations, bulk transformations.
- Keep for Claude: final decisions, edits to the user's files, anything that needs this conversation's context. Treat DeepSeek output as a draft and verify before acting on it. Apply code changes yourself.
- The agent mode is read-only by design. DeepSeek never writes files or runs commands.
- Privacy: everything attached or read in agent mode is sent to DeepSeek's servers (China). Secret-looking files (`.env*`, `*.pem`, keys, credentials) are blocked. For work or client code, confirm with the user that sending it is OK before the first call in that repo.
- Report the `[ds]` cost line to the user after each delegation, in one line.
- Peak hours (2x price) are Mon–Fri 01–04h and 06–10h UTC = 22–01h and 03–07h Brasília. The usage line shows PEAK when that applies.

## Setup

The key is read from `DEEPSEEK_API_KEY` or `~/.config/deepseek/api_key` (chmod 600). Never print, log or ask for the key in chat. Logs and sessions live in `~/.cache/deepseek/` (`ledger.jsonl`, `out/`, `sessions/`).
