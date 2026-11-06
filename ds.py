#!/usr/bin/env python3
"""DeepSeek helper for Claude Code: one-shot calls, sessions and a read-only agent.

The prompts are laid out for DeepSeek's automatic prefix cache: the system
prompt is fixed, attached files come in sorted order, and the variable task
comes last. The full answer goes to a file. Stdout gets only a preview, the
output path and the usage/cost line, which keeps Claude's context small.
"""
import argparse
import datetime as dt
import fnmatch
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

API = "https://api.deepseek.com"
MODELS = {"flash": "deepseek-flash", "pro": "deepseek-v4-pro"}
# USD per 1M tokens at PEAK; off-peak is half (api-docs.deepseek.com/quick_start/pricing)
PRICES = {
    "deepseek-flash": {"hit": 0.006, "miss": 0.30, "out": 1.20},
    "deepseek-v4-pro": {"hit": 0.044, "miss": 1.32, "out": 3.96},
}
PEAK_HOURS_UTC = {1, 2, 3, 6, 7, 8, 9}  # Mon-Fri

HOME = Path.home()
KEY_FILE = HOME / ".config/deepseek/api_key"
DATA = HOME / ".cache/deepseek"
OUT_DIR = DATA / "out"
SESS_DIR = DATA / "sessions"
LEDGER = DATA / "ledger.jsonl"

# Keep these byte-identical forever: any change invalidates every cached prefix.
SYSTEM = (
    "You are a senior software engineer working as a delegated assistant for another AI agent. "
    "Be precise and concise. Answer in the language of the task. "
    "When given files, ground your answer in them and cite paths and line numbers. "
    "When writing code, output complete, working code blocks."
)
AGENT_SYSTEM = SYSTEM + (
    " You have read-only tools to explore a codebase: list_dir, read_file, grep. "
    "Explore efficiently (grep before reading whole files), then give a final answer "
    "with concrete file:line references. Do not ask questions; decide and proceed."
)

SECRET_GLOBS = [".env", ".env.*", "*.pem", "*.key", "id_rsa*", "id_ed25519*", "*.p12",
                "*.pfx", "credentials*", "*secret*", ".npmrc", ".pypirc", ".netrc"]
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build",
             ".next", "target", ".cache", ".idea", ".gradle"}


def die(msg, code=1):
    print(f"ds: {msg}", file=sys.stderr)
    sys.exit(code)


def api_key():
    k = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not k and KEY_FILE.exists():
        k = KEY_FILE.read_text().strip()
    if not k:
        die(f"no API key. Set DEEPSEEK_API_KEY or write it to {KEY_FILE} (chmod 600).")
    return k


def http(method, path, body=None, timeout=600):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method, headers={
        "Authorization": f"Bearer {api_key()}",
        "Content-Type": "application/json",
    })
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:500]
            if e.code in (429, 500, 502, 503) and attempt < 3:
                time.sleep(2 ** attempt * 3)
                continue
            die(f"HTTP {e.code}: {detail}")
        except urllib.error.URLError as e:
            if attempt < 3:
                time.sleep(2 ** attempt * 3)
                continue
            die(f"network error: {e}")


def is_peak(now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    return now.weekday() < 5 and now.hour in PEAK_HOURS_UTC


def cost(model, hit, miss, out, peak):
    p = PRICES.get(model, PRICES["deepseek-flash"])
    usd = (hit * p["hit"] + miss * p["miss"] + out * p["out"]) / 1e6
    return usd if peak else usd / 2


def add_usage(acc, u):
    acc["hit"] += u.get("prompt_cache_hit_tokens", 0)
    acc["miss"] += u.get("prompt_cache_miss_tokens", 0)
    acc["out"] += u.get("completion_tokens", 0)


def chat(model, messages, tools=None, max_tokens=None):
    body = {"model": model, "messages": messages, "stream": False}
    if tools:
        body["tools"] = tools
    if max_tokens:
        body["max_tokens"] = max_tokens
    return http("POST", "/chat/completions", body)


def files_block(paths):
    """Attached files, sorted and with a fixed delimiter, so the same set gives the same prefix."""
    parts = []
    for p in sorted({str(Path(x).expanduser().resolve()) for x in paths}):
        fp = Path(p)
        if not fp.is_file():
            die(f"not a file: {p}")
        if is_secret(fp):
            die(f"refusing to send a secret-looking file: {p}")
        text = fp.read_text(errors="replace")
        parts.append(f"<file path=\"{p}\">\n{text}\n</file>")
    return "\n\n".join(parts)


def is_secret(p: Path):
    return any(fnmatch.fnmatch(p.name.lower(), g) for g in SECRET_GLOBS)


def slug(s, n=40):
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s.lower()).strip("-")
    return s[:n] or "task"


def finish(model, task, answer, reasoning, usage, peak, out_path, mode, extra=None):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if not out_path:
        out_path = OUT_DIR / f"{dt.datetime.now():%Y%m%d-%H%M%S}-{slug(task)}.md"
    out_path = Path(out_path).expanduser()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    body = answer or ""
    if reasoning:
        body += "\n\n<!-- reasoning -->\n<details><summary>reasoning</summary>\n\n" + reasoning + "\n</details>\n"
    out_path.write_text(body)

    usd = cost(model, usage["hit"], usage["miss"], usage["out"], peak)
    inp = usage["hit"] + usage["miss"]
    rate = (usage["hit"] / inp * 100) if inp else 0
    rec = {"ts": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "mode": mode,
           "model": model, "peak": peak, **usage, "usd": round(usd, 6), "out_file": str(out_path),
           "task": task[:120]}
    if extra:
        rec.update(extra)
    DATA.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a") as f:
        f.write(json.dumps(rec) + "\n")

    lines = (answer or "").splitlines()
    preview = "\n".join(lines[:PREVIEW])
    print(preview)
    if len(lines) > PREVIEW:
        print(f"... [{len(lines) - PREVIEW} more lines in file]")
    print(f"\n[ds] file: {out_path}")
    print(f"[ds] {model} | {'PEAK' if peak else 'off-peak'} | in {inp} (cache hit {usage['hit']} = {rate:.0f}%)"
          f" | out {usage['out']} | ${usd:.5f}" + (f" | {extra_str(extra)}" if extra else ""))


def extra_str(extra):
    return " | ".join(f"{k} {v}" for k, v in extra.items())


# ---------- one-shot / session ----------

def run_ask(a, task):
    model = MODELS["pro" if a.pro else "flash"]
    peak = is_peak()
    sess_path = SESS_DIR / f"{slug(a.session, 60)}.json" if a.session else None
    messages = [{"role": "system", "content": SYSTEM}]
    if sess_path and sess_path.exists():
        saved = json.loads(sess_path.read_text())
        messages = saved["messages"]
        if saved.get("model") and not a.pro and not a.flash:
            model = saved["model"]  # same model keeps the cache valid
    content = (files_block(a.file) + "\n\n" if a.file else "") + "TASK:\n" + task
    messages.append({"role": "user", "content": content})

    r = chat(model, messages, max_tokens=a.max_tokens)
    msg = r["choices"][0]["message"]
    usage = {"hit": 0, "miss": 0, "out": 0}
    add_usage(usage, r.get("usage", {}))
    if sess_path:
        messages.append({"role": "assistant", "content": msg.get("content") or ""})
        SESS_DIR.mkdir(parents=True, exist_ok=True)
        sess_path.write_text(json.dumps({"model": model, "messages": messages}))
    extra = {"session": a.session, "turn": sum(m["role"] == "user" for m in messages)} if a.session else None
    finish(model, task, msg.get("content"), msg.get("reasoning_content"), usage, peak, a.out, "ask", extra)


# ---------- agent (read-only tools) ----------

TOOLS = [
    {"type": "function", "function": {
        "name": "list_dir", "description": "List entries of a directory (relative to project root).",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "read_file", "description": "Read a text file with line numbers. Use offset/limit for big files.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}, "offset": {"type": "integer", "description": "1-based start line"},
            "limit": {"type": "integer", "description": "max lines (default 400)"}}, "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "grep", "description": "Regex search in files under a directory. Returns path:line: text.",
        "parameters": {"type": "object", "properties": {
            "pattern": {"type": "string"}, "path": {"type": "string", "description": "dir or file, default ."},
            "glob": {"type": "string", "description": "filename glob filter, e.g. *.py"}},
            "required": ["pattern"]}}},
]
TOOL_CAP = 20000


def safe_path(root: Path, rel: str) -> Path:
    p = (root / (rel or ".")).resolve()
    if p != root and root not in p.parents:
        raise ValueError("path outside project root")
    if is_secret(p):
        raise ValueError("secret-looking file is blocked")
    return p


def t_list_dir(root, path="."):
    p = safe_path(root, path)
    out = []
    for e in sorted(p.iterdir()):
        if e.name in SKIP_DIRS:
            continue
        out.append(e.name + ("/" if e.is_dir() else ""))
    return "\n".join(out[:500]) or "(empty)"


def t_read_file(root, path, offset=1, limit=400):
    p = safe_path(root, path)
    lines = p.read_text(errors="replace").splitlines()
    offset = max(1, offset or 1)
    chunk = lines[offset - 1: offset - 1 + (limit or 400)]
    body = "\n".join(f"{i}\t{l}" for i, l in enumerate(chunk, offset))
    tail = f"\n[showing {offset}-{offset + len(chunk) - 1} of {len(lines)} lines]"
    return body + tail


def t_grep(root, pattern, path=".", glob=None):
    rx = re.compile(pattern)
    base = safe_path(root, path)
    files = [base] if base.is_file() else None
    hits = []
    if files is None:
        files = []
        for dp, dns, fns in os.walk(base):
            dns[:] = sorted(d for d in dns if d not in SKIP_DIRS)
            for fn in sorted(fns):
                if glob and not fnmatch.fnmatch(fn, glob):
                    continue
                files.append(Path(dp) / fn)
    for f in files:
        if is_secret(f):
            continue
        try:
            if f.stat().st_size > 2_000_000:
                continue
            for i, line in enumerate(f.read_text(errors="strict").splitlines(), 1):
                if rx.search(line):
                    hits.append(f"{f.relative_to(root)}:{i}: {line.strip()[:200]}")
                    if len(hits) >= 200:
                        return "\n".join(hits) + "\n[truncated at 200 matches]"
        except (UnicodeDecodeError, OSError):
            continue
    return "\n".join(hits) or "(no matches)"


def run_tool(root, name, args):
    try:
        fn = {"list_dir": t_list_dir, "read_file": t_read_file, "grep": t_grep}[name]
        res = fn(root, **args)
    except Exception as e:  # report errors to the model instead of crashing
        res = f"ERROR: {e}"
    if len(res) > TOOL_CAP:
        res = res[:TOOL_CAP] + f"\n[truncated: {len(res)} chars total]"
    return res


def run_agent(a, task):
    # Agent mode defaults to pro: exploration quality matters more than its few cents.
    model = MODELS["flash" if a.flash else "pro"]
    peak = is_peak()
    root = Path(a.agent).expanduser().resolve()
    if not root.is_dir():
        die(f"agent root is not a directory: {root}")
    messages = [{"role": "system", "content": AGENT_SYSTEM}]
    content = (files_block(a.file) + "\n\n" if a.file else "") + f"PROJECT ROOT: {root}\nTASK:\n{task}"
    messages.append({"role": "user", "content": content})
    usage = {"hit": 0, "miss": 0, "out": 0}
    steps, answer, reasoning = 0, None, None
    while steps < a.max_steps:
        steps += 1
        r = chat(model, messages, tools=TOOLS, max_tokens=a.max_tokens)
        add_usage(usage, r.get("usage", {}))
        msg = r["choices"][0]["message"]
        calls = msg.get("tool_calls") or []
        # Append the assistant turn exactly as returned so the prefix stays stable.
        turn = {"role": "assistant", "content": msg.get("content") or ""}
        if msg.get("reasoning_content"):
            turn["reasoning_content"] = msg["reasoning_content"]
        if calls:
            turn["tool_calls"] = calls
        messages.append(turn)
        if not calls:
            answer, reasoning = msg.get("content"), msg.get("reasoning_content")
            break
        for c in calls:
            try:
                args = json.loads(c["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            res = run_tool(root, c["function"]["name"], args)
            print(f"[ds] step {steps}: {c['function']['name']}({json.dumps(args)[:100]})", file=sys.stderr)
            messages.append({"role": "tool", "tool_call_id": c["id"], "content": res})
        if usage["hit"] + usage["miss"] > a.max_input:
            messages.append({"role": "user", "content": "Token budget reached. Give your final answer now, no more tools."})
            r = chat(model, messages, max_tokens=a.max_tokens)
            add_usage(usage, r.get("usage", {}))
            answer = r["choices"][0]["message"].get("content")
            break
    if answer is None:
        messages.append({"role": "user", "content": "Step limit reached. Give your final answer now, no more tools."})
        r = chat(model, messages, max_tokens=a.max_tokens)
        add_usage(usage, r.get("usage", {}))
        answer = r["choices"][0]["message"].get("content")
    finish(model, task, answer, reasoning, usage, peak, a.out, "agent", {"steps": steps})


# ---------- utilities ----------

def run_balance():
    r = http("GET", "/user/balance", timeout=30)
    for b in r.get("balance_infos", []):
        print(f"{b['currency']} {b['total_balance']} (topped up {b['topped_up_balance']}, granted {b['granted_balance']})")
    print("available" if r.get("is_available") else "NOT available (insufficient balance)")


def run_stats(days):
    if not LEDGER.exists():
        print("no calls yet")
        return
    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
    n = hit = miss = out = 0
    usd = 0.0
    for line in LEDGER.read_text().splitlines():
        r = json.loads(line)
        if dt.datetime.fromisoformat(r["ts"]) < since:
            continue
        n += 1
        hit += r["hit"]; miss += r["miss"]; out += r["out"]; usd += r["usd"]
    inp = hit + miss
    print(f"last {days}d: {n} calls | in {inp} (hit {hit} = {hit / inp * 100 if inp else 0:.0f}%) | out {out} | ${usd:.4f}")
    print(f"peak now: {'YES (2x price)' if is_peak() else 'no (off-peak)'}")


PREVIEW = 20


def main():
    global PREVIEW
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("task", nargs="?", help="task text, or '-' to read from stdin")
    ap.add_argument("-f", "--file", action="append", default=[], help="attach a file (repeatable)")
    ap.add_argument("--pro", action="store_true", help="use deepseek-v4-pro (default: flash; --agent defaults to pro)")
    ap.add_argument("--flash", action="store_true", help="force flash (in --agent, or when a session used pro)")
    ap.add_argument("-s", "--session", help="named conversation; follow-ups reuse the cached prefix")
    ap.add_argument("--agent", metavar="ROOT", help="agent mode: DeepSeek explores ROOT with read-only tools")
    ap.add_argument("--max-steps", type=int, default=25)
    ap.add_argument("--max-input", type=int, default=1_500_000, help="agent input-token budget")
    ap.add_argument("--max-tokens", type=int, default=None, help="max output tokens per call")
    ap.add_argument("-o", "--out", help="output file (default ~/.cache/deepseek/out/...)")
    ap.add_argument("--preview", type=int, default=20, help="lines of the answer to print")
    ap.add_argument("--balance", action="store_true", help="show account balance")
    ap.add_argument("--stats", type=int, nargs="?", const=7, metavar="DAYS", help="spend summary")
    ap.add_argument("--reset-session", metavar="NAME", help="delete a session")
    a = ap.parse_args()
    PREVIEW = a.preview

    if a.balance:
        return run_balance()
    if a.stats is not None:
        return run_stats(a.stats)
    if a.reset_session:
        p = SESS_DIR / f"{slug(a.reset_session, 60)}.json"
        p.unlink(missing_ok=True)
        return print(f"session '{a.reset_session}' cleared")
    if not a.task:
        ap.error("task is required")
    task = sys.stdin.read() if a.task == "-" else a.task
    if a.agent:
        if a.session:
            ap.error("--session and --agent can't be combined")
        return run_agent(a, task)
    run_ask(a, task)


if __name__ == "__main__":
    main()
