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
from datetime import date, datetime
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
def due_of(t):
    """The task's optional date (YYYY-MM-DD) or '' when it has none or it is not a valid date."""
    d = t.get("due") or ""
    try:
        date.fromisoformat(d)
        return d
    except (TypeError, ValueError):
        return ""


def is_later(t):
    """Parked in Later. A dated Later task comes back to Today on its day (and stays there if overdue),
    decided when reading, so nothing in the file is rewritten."""
    if t.get("later") is not True:
        return False
    d = due_of(t)
    return not (d and d <= today())


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


def when(t):
    """A dated task's date as the app shows it, e.g. 'Mon 23 Nov 14:30', or '' for an undated task."""
    d = due_of(t)
    if not d:
        return ""
    day = date.fromisoformat(d)
    out = f"{day:%a} {day.day} {day:%b}"  # not %-d, which Windows does not support
    if t.get("dueTime"):
        out += " " + t["dueTime"]
    return out


def urgency(t):
    """overdue, today, tomorrow, this week or later, the same cue the app colours."""
    days = (date.fromisoformat(due_of(t)) - date.fromisoformat(today())).days
    if days < 0:
        return "overdue"
    return {0: "today", 1: "tomorrow"}.get(days, "this week" if days <= 7 else "later")


def line(t):
    w = when(t)
    return f"{t['id']} | {'*' if t.get('starred') else ' '} {t['title']}" + (f" [{w}]" if w else "")


def upcoming(tasks):
    """Dated tasks that are not done, soonest first (a task with a time comes after the all-day ones that day)."""
    rows = [t for t in tasks if due_of(t) and not t.get("done")]
    return sorted(rows, key=lambda t: (due_of(t), t.get("dueTime") or ""))


def check_date(due, due_time):
    """Tidy and check an optional date and time. Returns (due, due_time) or raises TaskError."""
    due, due_time = (due or "").strip(), (due_time or "").strip()
    if due:
        try:
            due = date.fromisoformat(due).isoformat()
        except ValueError:
            raise TaskError(f"Nothing changed: '{due}' is not a date. Use YYYY-MM-DD, e.g. {today()}.")
    if due_time:
        if not due:
            raise TaskError("Nothing changed: a time needs a date too (due=YYYY-MM-DD).")
        try:
            due_time = datetime.strptime(due_time, "%H:%M").strftime("%H:%M")
        except ValueError:
            raise TaskError(f"Nothing changed: '{due_time}' is not a time. Use HH:MM (24-hour), e.g. 14:30.")
    return due, due_time


def set_date(t, due, due_time):
    """Write the date fields; leaving them off when empty so undated tasks look exactly as before."""
    for key, value in (("due", due), ("dueTime", due_time)):
        if value:
            t[key] = value
        else:
            t.pop(key, None)


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
    soon = [t for t in upcoming(tasks) if t not in open_today]
    if soon:
        out.append(f"Upcoming ({len(soon)} dated): " + "; ".join(
            f"{when(t)} {t['title']}" for t in soon[:3]))
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
    """List tasks as 'id | * title [date]' (* = starred; the date only on dated tasks).
    scope: today (default), later, done (finished today) or upcoming (dated, not done, soonest first,
    with overdue/today/tomorrow marked)."""
    data, _ = store.load("tasks.json")
    tasks = data["tasks"]
    if scope == "later":
        rows = ordered_open(tasks, True)
    elif scope == "done":
        rows = [t for t in tasks if t.get("done") and t.get("doneDay") == today()]
    elif scope == "upcoming":
        rows = upcoming(tasks)
        if not rows:
            return "No upcoming dated tasks."
        cue = {"overdue": " OVERDUE", "today": " TODAY", "tomorrow": " TOMORROW"}
        more = f"\n...and {len(rows) - limit} more" if len(rows) > limit else ""
        return "\n".join(line(t) + cue.get(urgency(t), "") for t in rows[:limit]) + more
    else:
        rows = ordered_open(tasks, False)
    if not rows:
        return f"No {scope} tasks."
    more = f"\n...and {len(rows) - limit} more" if len(rows) > limit else ""
    return "\n".join(line(t) for t in rows[:limit]) + more


@mcp.tool()
def add_task(title: str, later: bool = False, starred: bool = False, position: str = "bottom",
             due: str = "", due_time: str = "") -> str:
    """Add a task. later=True parks it in Later instead of Today. starred pins it to the top.
    position: bottom (default) or top of its list.
    due: optional date YYYY-MM-DD (for calls, deadlines); due_time: optional HH:MM, needs due.
    A dated task in Later shows in Today automatically on its day."""
    title = " ".join(title.split())[:600]
    if not title:
        return "Nothing to add."
    try:
        due, due_time = check_date(due, due_time)
    except TaskError as e:
        return str(e)

    def change(data):
        t = {"id": new_id(), "title": title, "done": False, "day": today(), "sessions": 0,
             "updated": now_ms(), "later": later, "starred": starred}
        set_date(t, due, due_time)
        data["tasks"].append(t)
        group = [x for x in ordered_open(data["tasks"], is_later(t)) if bool(x.get("starred")) == starred]
        renumber(group, t, position if position in ("top", "bottom") else "bottom")
        return f"Added: {line(t)}" + (" (Later)" if is_later(t) else "")
    return edit_tasks(change, "Add task via MCP")


@mcp.tool()
def update_task(task: str, action: str, new_title: str = "", due: str = "", due_time: str = "") -> str:
    """Change one task. task = its id or part of its title.
    action: done, undone, top, bottom, later, today, star, unstar, rename (with new_title),
    date (with due YYYY-MM-DD and optional due_time HH:MM; an empty due removes the date)."""
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
        elif action == "date":
            set_date(t, *check_date(due, due_time))
        else:
            raise TaskError("Nothing changed: unknown action. Use done, undone, top, bottom, later, today, star, unstar, rename or date.")
        t["updated"] = stamp
        return f"{action}: {line(t)}"
    return edit_tasks(change, f"{action} task via MCP")


if __name__ == "__main__":
    mcp.run()
