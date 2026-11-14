# Tomato Timer MCP

A small MCP server that lets Claude, Claude Code or ChatGPT read and change the task list in
[Tomato Timer](https://github.com/jay1zzzo/tomato-timer). Tomato Timer already syncs its tasks to a
private GitHub repo; this server reads and writes that same file, so changes made through an AI
show up in the app on the next sync (and the other way round).

## Tools (kept to four on purpose, so it costs very little to leave connected)

| Tool | What it does |
| --- | --- |
| `get_context` | Short snapshot: today's open tasks in order, how many are in Later, what was done today, the next content-calendar posts |
| `list_tasks` | List `today`, `later` or `done` tasks as `id \| * title` |
| `add_task` | Add a task (to Today or Later, starred or not, top or bottom) |
| `update_task` | Mark done or undone, send to top or bottom, move between Today and Later, star or unstar, rename |

Replies are short lists, not whole files, so an AI can stay up to date without using many tokens.

## How it keeps your data safe

* It follows the same ordering rules as the app (starred tasks first, then position).
* It only touches the rows it changes, and keeps everything else in the file as it was.
* It never deletes tasks.
* Writes use GitHub's version check. If the app syncs at the same moment, the server reloads and
  tries again instead of overwriting it.

## Set up

1. **Install** (Python 3.10 or newer):
   ```
   python -m venv .venv
   .venv/bin/pip install -r requirements.txt        # Windows: .venv\Scripts\pip install -r requirements.txt
   ```
2. **Make a token.** GitHub > Settings > Developer settings > Fine-grained tokens. Give it access to
   the `tomato-data` repo only, with Contents set to Read and write. (The token you already use for
   syncing in the app works too.)
3. **Connect it.**

   Claude Code:
   ```
   claude mcp add tomato-timer -e TOMATO_GITHUB_TOKEN=YOUR_TOKEN -- /full/path/.venv/bin/python /full/path/server.py
   ```
   Claude Desktop: add this to `claude_desktop_config.json` under `mcpServers`, then restart it:
   ```json
   "tomato-timer": {
     "command": "/full/path/.venv/bin/python",
     "args": ["/full/path/server.py"],
     "env": { "TOMATO_GITHUB_TOKEN": "YOUR_TOKEN" }
   }
   ```
4. Ask: "What's on my list today?" or "Put 'email the plumber' at the top of my tasks."

Never commit your token. Settings: `TOMATO_REPO` (required, e.g. `your-username/tomato-data`) and `TOMATO_TZ`
(default `Europe/London`).

## Tests

`python test_server.py` runs the server over the real MCP protocol on made-up tasks, and
`python test_github_mock.py` checks the GitHub path, including the "app synced at the same time" retry,
against a fake GitHub API.

## Limits

This version runs on your own computer, so it works in Claude Code and Claude Desktop. ChatGPT and
claude.ai on the web need a hosted version (a small web server with a password) which is the next step.
