"""End-to-end test: starts the server over the real MCP protocol (stdio) on made-up tasks in a temp folder.

Run:  python test_server.py      (or: pytest test_server.py)
"""
import asyncio
import json
import os
import sys
import tempfile
import time

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def make_data(folder):
    """Seven made-up tasks: five open today (Echo starred), one in Later, one finished long ago."""
    now = int(time.time() * 1000)

    def task(i, title, **extra):
        base = dict(id=f"t{i}", title=title, done=False, day="2026-11-14", sessions=0, updated=now)
        return {**base, **extra}

    tasks = [
        task(1, "Alpha", ord=10), task(2, "Bravo", ord=20), task(3, "Charlie", ord=30),
        task(4, "Delta", ord=40), task(5, "Echo", ord=50, starred=True),
        task(6, "Foxtrot", later=True), task(7, "Old done", done=True, doneDay="2026-11-01"),
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

            # starred tasks sort first, then by position
            assert ids(await call("list_tasks")) == ["t5", "t1", "t2", "t3", "t4"]

            ctx = await call("get_context")
            assert "5 open, 1 later, 0 done" in ctx and "Coffee morning recap" in ctx

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

    # it never deletes: all seven original tasks plus the new one are still in the file
    with open(os.path.join(folder, "tasks.json")) as f:
        saved = json.load(f)["tasks"]
    assert len(saved) == 8 and {f"t{i}" for i in range(1, 8)} <= {t["id"] for t in saved}


def test_server_over_mcp():
    with tempfile.TemporaryDirectory() as folder:
        make_data(folder)
        asyncio.run(run(folder))


if __name__ == "__main__":
    test_server_over_mcp()
    print("test_server: all checks passed")
