"""End-to-end test: starts the server over the real MCP protocol (stdio) on made-up tasks in a temp folder.

Run:  python test_server.py      (or: pytest test_server.py)
"""
import asyncio
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


TODAY = datetime.now(ZoneInfo("Europe/London")).date()


def day(offset):
    """A date relative to today, as YYYY-MM-DD."""
    return (TODAY + timedelta(days=offset)).isoformat()


def make_data(folder):
    """Seven made-up tasks: five open today (Echo starred), one in Later, one finished long ago.
    Plus four dated ones: an overdue call parked in Later, a deadline in Later due today, an interview
    next week in Later, and a finished dated task."""
    now = int(time.time() * 1000)

    def task(i, title, **extra):
        base = dict(id=f"t{i}", title=title, done=False, day="2026-11-14", sessions=0, updated=now)
        return {**base, **extra}

    tasks = [
        task(1, "Alpha", ord=10), task(2, "Bravo", ord=20), task(3, "Charlie", ord=30),
        task(4, "Delta", ord=40), task(5, "Echo", ord=50, starred=True),
        task(6, "Foxtrot", later=True), task(7, "Old done", done=True, doneDay="2026-11-01"),
        task(8, "Call the bank", later=True, due=day(-1)),
        task(9, "Send invoice", later=True, due=day(0), dueTime="17:00"),
        task(10, "Interview", later=True, due=day(6), dueTime="10:30"),
        task(11, "Dated but done", done=True, doneDay=day(-3), due=day(-3)),
    ]
    posts = [{"id": "p1", "title": "Coffee morning recap", "date": "2099-01-01", "ready": True}]
    with open(os.path.join(folder, "tasks.json"), "w") as f:
        json.dump({"v": 1, "tasks": tasks, "deleted": []}, f)
    with open(os.path.join(folder, "calendar.json"), "w") as f:
        json.dump({"v": 1, "posts": posts, "deleted": []}, f)


def ids(listing):
    """Task ids from a 'id | * title' listing, in order."""
    return [row.split(" | ")[0] for row in listing.splitlines() if " | " in row]


async def run(folder):
    env = {**os.environ, "TOMATO_DATA_DIR": folder}
    params = StdioServerParameters(command=sys.executable, args=["server.py"], env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as s:
            await s.initialize()

            async def call(name, **args):
                return (await s.call_tool(name, args)).content[0].text

            tools = {t.name for t in (await s.list_tools()).tools}
            assert tools == {"get_context", "list_tasks", "add_task", "update_task"}

            # starred tasks sort first, then by position; dated Later tasks join Today on (or after) their day
            assert ids(await call("list_tasks")) == ["t5", "t1", "t2", "t3", "t4", "t8", "t9"]
            assert ids(await call("list_tasks", scope="later")) == ["t6", "t10"]

            ctx = await call("get_context")
            assert "7 open, 2 later, 0 done" in ctx and "Coffee morning recap" in ctx
            assert "Upcoming (1 dated)" in ctx and "Interview" in ctx

            # upcoming: dated and not done, soonest first, with the urgency marked
            up = await call("list_tasks", scope="upcoming")
            assert ids(up) == ["t8", "t9", "t10"]
            rows = up.splitlines()
            assert rows[0].endswith("OVERDUE") and rows[1].endswith("TODAY") and "17:00" in rows[1]
            assert "10:30]" in rows[2] and not rows[2].endswith(("OVERDUE", "TODAY", "TOMORROW"))

            # adding with a date and time; bad dates change nothing
            reply = await call("add_task", title="Dentist", later=True, due=day(1), due_time="9:05")
            assert reply.startswith("Added:") and "09:05]" in reply and "(Later)" in reply
            assert ids(await call("list_tasks", scope="upcoming"))[2] == reply.removeprefix("Added: ").split(" | ")[0]
            assert "TOMORROW" in (await call("list_tasks", scope="upcoming")).splitlines()[2]
            assert "not a date" in await call("add_task", title="Bad", due="31/12/2026")
            assert "needs a date" in await call("add_task", title="Bad", due_time="10:00")
            assert "not a time" in await call("add_task", title="Bad", due=day(2), due_time="25:99")

            # changing and removing a date
            await call("update_task", task="Foxtrot", action="date", due=day(3))
            assert "t6" in ids(await call("list_tasks", scope="upcoming"))
            await call("update_task", task="Foxtrot", action="date")
            assert "t6" not in ids(await call("list_tasks", scope="upcoming"))

            # adding: extra spaces are tidied, 'top' puts it first among unstarred tasks
            reply = await call("add_task", title="  Email   the plumber back ", position="top")
            assert reply.startswith("Added:") and "Email the plumber back" in reply
            new_id = reply.removeprefix("Added: ").split(" | ")[0]
            assert ids(await call("list_tasks"))[1:3] == [new_id, "t1"]

            # moving, parking, finishing
            await call("update_task", task="delta", action="top")
            assert ids(await call("list_tasks"))[1] == "t4"
            await call("update_task", task="Charlie", action="later")
            assert "t3" in ids(await call("list_tasks", scope="later"))
            await call("update_task", task="bravo", action="done")
            assert ids(await call("list_tasks", scope="done")) == ["t2"]

            # mistakes come back as plain messages the AI can act on, and change nothing
            assert "More than one match" in await call("update_task", task="a", action="star")
            assert "No task matches" in await call("update_task", task="zzz", action="done")
            assert "unknown action" in await call("update_task", task="t1", action="explode")
            assert "already done" in await call("update_task", task="t2", action="top")

    # it never deletes: all eleven original tasks plus the two new ones are still in the file
    with open(os.path.join(folder, "tasks.json")) as f:
        saved = {t["id"]: t for t in json.load(f)["tasks"]}
    assert len(saved) == 13 and {f"t{i}" for i in range(1, 12)} <= set(saved)
    # undated tasks never gain date fields; a removed date leaves no empty field behind
    assert all("due" not in saved[f"t{i}"] and "dueTime" not in saved[f"t{i}"] for i in range(1, 8))
    assert saved["t8"]["later"] is True  # shown in Today by date, but the file was not rewritten


def test_server_over_mcp():
    with tempfile.TemporaryDirectory() as folder:
        make_data(folder)
        asyncio.run(run(folder))


if __name__ == "__main__":
    test_server_over_mcp()
    print("test_server: all checks passed")
