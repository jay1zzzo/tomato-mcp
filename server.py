"""Tomato Timer MCP server.

Lets an AI app that supports MCP (tested with Claude Desktop and Claude Code) read and change the
task list that Tomato Timer, a personal app, syncs to a private GitHub repo. It is deliberately small: four tools with
short replies, so it costs very little to keep connected.

Settings come from environment variables:
  TOMATO_GITHUB_TOKEN  token that can read and write the data repo (required for GitHub mode)
  TOMATO_REPO          repo holding tasks.json, required in GitHub mode, e.g. your-username/tomato-data
  TOMATO_DATA_DIR      folder containing tasks.json and calendar.json (local mode, used for testing)
  TOMATO_TZ            time zone for "today", default Europe/London
"""
import base64
import json
import os
import random
import string
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("tomato-timer")
TZ = ZoneInfo(os.environ.get("TOMATO_TZ", "Europe/London"))
REPO = os.environ.get("TOMATO_REPO", "")
TOKEN = os.environ.get("TOMATO_GITHUB_TOKEN", "")
DATA_DIR = os.environ.get("TOMATO_DATA_DIR", "")


# ---------- storage: a local folder (testing) or the private GitHub repo ----------
class Conflict(Exception):
    """The app synced while we were editing; reload and try again."""


class TaskError(Exception):
    """Something the AI can fix and retry (no match, several matches, unknown action). The text is shown to it."""


class Store:
    def load(self, name):
        """Return (data, version) for tasks.json / calendar.json."""
        if DATA_DIR:
            path = os.path.join(DATA_DIR, name)
            with open(path, encoding="utf-8") as f:
                return json.load(f), os.path.getmtime(path)
        r = httpx.get(f"https://api.github.com/repos/{REPO}/contents/{name}",
                      headers=self._headers(), timeout=20)
        r.raise_for_status()
        j = r.json()
        return json.loads(base64.b64decode(j["content"]).decode("utf-8")), j["sha"]

    def save(self, name, data, version, message):
        text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        if DATA_DIR:
            with open(os.path.join(DATA_DIR, name), "w", encoding="utf-8") as f:
                f.write(text)
            return
        body = {"message": message, "sha": version,
                "content": base64.b64encode(text.encode("utf-8")).decode("ascii")}
        r = httpx.put(f"https://api.github.com/repos/{REPO}/contents/{name}",
                      headers=self._headers(), json=body, timeout=20)
        if r.status_code == 409:
            raise Conflict()
        r.raise_for_status()

    def _headers(self):
        if not TOKEN:
            raise RuntimeError("Set TOMATO_GITHUB_TOKEN (or TOMATO_DATA_DIR for local testing).")
        return {"Authorization": f"Bearer {TOKEN}", "Accept": "application/vnd.github+json"}


store = Store()


def now_ms():
    return int(time.time() * 1000)


def today():
    return datetime.now(TZ).strftime("%Y-%m-%d")


def new_id():
    n, chars, out = now_ms(), string.digits + string.ascii_lowercase, ""
    while n:
        n, r = divmod(n, 36)
        out = chars[r] + out
    return out + "".join(random.choices(chars, k=4))


# ---------- the same rules the app uses ----------
def is_later(t):
    return t.get("later") is True


def is_past(t):
    return bool(t.get("done") and t.get("doneDay") and t["doneDay"] < today())


def by_ord(tasks):
    def key(pair):
        i, t = pair
        return (t["ord"] if t.get("ord") is not None else 1e12, i)
    return [t for _, t in sorted(enumerate(tasks), key=key)]


def ordered_open(tasks, later):
    """Open tasks in the order the app shows them: starred first, then by position."""
    pool = [t for t in tasks if not t.get("done") and is_later(t) == later and not is_past(t)]
    return by_ord([t for t in pool if t.get("starred")]) + by_ord([t for t in pool if not t.get("starred")])


def line(t):
    return f"{t['id']} | {'*' if t.get('starred') else ' '} {t['title']}"


def find(tasks, ref):
    """Match a task by id, or by part of its title. Raises TaskError if there is not exactly one match."""
    ref = ref.strip()
    for t in tasks:
        if t["id"] == ref:
            return t
    hits = [t for t in tasks if ref.lower() in t["title"].lower() and not is_past(t)]
    exact = [t for t in hits if t["title"].lower() == ref.lower()]
    hits = exact or hits
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise TaskError(f"No task matches '{ref}'.")
    raise TaskError("More than one match, use an id:\n" + "\n".join(line(t) for t in hits[:6]))


def edit_tasks(change, message):
    """Load tasks.json, apply change(data) -> reply, save. Retries if the app synced meanwhile."""
    for _ in range(3):
        data, version = store.load("tasks.json")
        try:
            reply = change(data)
        except TaskError as e:
            return str(e)
        try:
            store.save("tasks.json", data, version, message)
            return reply
        except Conflict:
            continue
    return "The task list changed while saving. Try again."


def renumber(group, moved, where):
    """Give a group of tasks new positions with `moved` at the top or bottom. Only changed rows are stamped."""
    group = [t for t in group if t["id"] != moved["id"]]
    if where == "top":
        group.insert(0, moved)
    else:
        group.append(moved)
    stamp = now_ms()
    for i, t in enumerate(group):
        if t.get("ord") != (i + 1) * 10:
            t["ord"] = (i + 1) * 10
            t["updated"] = stamp


# ---------- tools ----------
@mcp.tool()
def get_context() -> str:
    """Short snapshot of the user's day: today's open tasks in order, how many are parked for later,
    what was finished today, and the next content-calendar posts. Call this first."""
    data, _ = store.load("tasks.json")
    tasks = data["tasks"]
    open_today = ordered_open(tasks, False)
    later_n = len(ordered_open(tasks, True))
    done_today = [t for t in tasks if t.get("done") and t.get("doneDay") == today()]
    out = [f"Today {today()}: {len(open_today)} open, {later_n} later, {len(done_today)} done"]
    out += [line(t) for t in open_today[:10]]
    if len(open_today) > 10:
        out.append(f"...and {len(open_today) - 10} more")
    try:
        cal, _ = store.load("calendar.json")
        nxt = sorted((p for p in cal.get("posts", []) if p.get("date", "") >= today() and not p.get("doneDay")),
                     key=lambda p: p["date"])[:3]
        if nxt:
            out.append("Next posts: " + "; ".join(
                f"{p['date']} {p.get('title', '')}" + (" (ready)" if p.get("ready") else "") for p in nxt))
    except (httpx.HTTPError, OSError, ValueError, KeyError):
        pass  # the calendar is optional; the task snapshot is still useful without it
    return "\n".join(out)


@mcp.tool()
def list_tasks(scope: str = "today", limit: int = 15) -> str:
    """List tasks as 'id | * title' (* = starred). scope: today (default), later, or done (finished today)."""
    data, _ = store.load("tasks.json")
    tasks = data["tasks"]
    if scope == "later":
        rows = ordered_open(tasks, True)
    elif scope == "done":
        rows = [t for t in tasks if t.get("done") and t.get("doneDay") == today()]
    else:
        rows = ordered_open(tasks, False)
    if not rows:
        return f"No {scope} tasks."
    more = f"\n...and {len(rows) - limit} more" if len(rows) > limit else ""
    return "\n".join(line(t) for t in rows[:limit]) + more


@mcp.tool()
def add_task(title: str, later: bool = False, starred: bool = False, position: str = "bottom") -> str:
    """Add a task. later=True parks it in Later instead of Today. starred pins it to the top.
    position: bottom (default) or top of its list."""
    title = " ".join(title.split())[:600]
    if not title:
        return "Nothing to add."

    def change(data):
        t = {"id": new_id(), "title": title, "done": False, "day": today(), "sessions": 0,
             "updated": now_ms(), "later": later, "starred": starred}
        data["tasks"].append(t)
        group = [x for x in ordered_open(data["tasks"], later) if bool(x.get("starred")) == starred]
        renumber(group, t, position if position in ("top", "bottom") else "bottom")
        return f"Added: {line(t)}" + (" (Later)" if later else "")
    return edit_tasks(change, "Add task via MCP")


@mcp.tool()
def update_task(task: str, action: str, new_title: str = "") -> str:
    """Change one task. task = its id or part of its title.
    action: done, undone, top, bottom, later, today, star, unstar, rename (with new_title)."""
    def change(data):
        t = find(data["tasks"], task)
        stamp = now_ms()
        if action == "done":
            t.update(done=True, doneDay=today(), completedAt=stamp)
        elif action == "undone":
            t.update(done=False, doneDay=None, completedAt=None)
        elif action in ("top", "bottom"):
            if t.get("done"):
                raise TaskError("Nothing changed: that task is already done.")
            group = [x for x in ordered_open(data["tasks"], is_later(t))
                     if bool(x.get("starred")) == bool(t.get("starred"))]
            renumber(group, t, action)
        elif action in ("later", "today"):
            t["later"] = action == "later"
        elif action in ("star", "unstar"):
            t["starred"] = action == "star"
        elif action == "rename" and new_title.strip():
            t["title"] = " ".join(new_title.split())[:600]
        else:
            raise TaskError("Nothing changed: unknown action. Use done, undone, top, bottom, later, today, star, unstar or rename.")
        t["updated"] = stamp
        return f"{action}: {line(t)}"
    return edit_tasks(change, f"{action} task via MCP")


if __name__ == "__main__":
    mcp.run()
