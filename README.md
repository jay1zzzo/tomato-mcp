# Tomato Timer MCP

A small MCP server that lets Claude, Claude Code or ChatGPT read and change the task list in
Tomato Timer, a personal productivity app I built for my own use. **Tomato Timer is not a public
product and isn't available to install**; this repo is published to show how the connection works.
The app syncs its tasks to a private GitHub repo, and this server reads and writes that same file, so
changes made through an AI show up in the app on the next sync (and the other way round).

Want to reuse the idea? The server works with any private GitHub repo that holds a `tasks.json` in the
same shape as the one the app writes (see `test_server.py` for sample data).

## Tools (kept to four on purpose, so it costs very little to leave connected)

| Tool | What it does |
| --- | --- |
| `get_context` | Short snapshot: today's open tasks in order, how many are in Later, what was done today, the next content-calendar posts |
| `list_tasks` | List `today`, `later` or `done` tasks as `id \| * title` |
| `add_task` | Add a task (to Today or Later, starred or not, top or bottom) |
| `update_task` | Mark done or undone, send to top or bottom, move between Today and Later, star or unstar, rename |

Replies are short lists, not whole files, so an AI can stay up to date without using many tokens.

## How it keeps the data safe

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
   the `tomato-data` repo only, with Contents set to Read and write.
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

This version runs on the computer it is installed on, so it works with Claude Code and Claude Desktop.
ChatGPT and claude.ai on the web would need a hosted version (a small web server with a login). That
is a possible next step for my own use, not something that exists yet.
