"""Checks the GitHub code path against a fake GitHub API (no network, no real token needed).

Run:  python test_github_mock.py      (or: pytest test_github_mock.py)
"""
import base64
import json
import os

os.environ.pop("TOMATO_DATA_DIR", None)
os.environ["TOMATO_GITHUB_TOKEN"] = "fake"
os.environ["TOMATO_REPO"] = "someone/tomato-data"

import httpx  # noqa: E402

import server  # noqa: E402


class FakeResponse:
    def __init__(self, code, body=None):
        self.status_code, self._body = code, body

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=None)


class FakeGitHub:
    """One file, a version (sha) that changes on every write, and a way to simulate the app syncing mid-edit."""

    def __init__(self, conflicts):
        task = {"id": "a1", "title": "Alpha", "done": False, "day": "2026-11-14",
                "sessions": 0, "updated": 1, "ord": 10}
        self.text = json.dumps({"v": 1, "tasks": [task], "deleted": []})
        self.sha, self.calls, self.conflicts = 1, [], conflicts

    def get(self, url, headers, timeout):
        assert headers["Authorization"] == "Bearer fake"
        self.calls.append("GET")
        wrapped = base64.encodebytes(self.text.encode()).decode()  # GitHub wraps base64 in newlines
        return FakeResponse(200, {"content": wrapped, "sha": f"s{self.sha}"})

    def put(self, url, headers, json, timeout):  # noqa: A002 - mirrors httpx's argument name
        self.calls.append("PUT")
        if self.conflicts > 0:  # the app synced while we were editing
            self.conflicts -= 1
            self.sha += 1
            return FakeResponse(409)
        if json["sha"] != f"s{self.sha}":
            return FakeResponse(409)
        self.text = base64.b64decode(json["content"]).decode()
        self.sha += 1
        return FakeResponse(200, {})


def install(fake):
    httpx.get, httpx.put = fake.get, fake.put


def test_retries_after_sync_clash():
    fake = FakeGitHub(conflicts=1)
    install(fake)
    reply = server.add_task("From the MCP", position="top")
    assert reply.startswith("Added:")
    assert fake.calls == ["GET", "PUT", "GET", "PUT"]  # reloaded the new version, then saved
    tasks = json.loads(fake.text)["tasks"]
    assert [t["title"] for t in tasks] == ["Alpha", "From the MCP"]
    assert tasks[1]["ord"] < tasks[0]["ord"]  # the new task is on top; the app's data was kept


def test_gives_up_politely_if_the_clash_never_clears():
    fake = FakeGitHub(conflicts=99)
    install(fake)
    reply = server.add_task("Never saved")
    assert "Try again" in reply
    assert fake.calls.count("PUT") == 3
    assert "Never saved" not in fake.text  # nothing was half-written


if __name__ == "__main__":
    test_retries_after_sync_clash()
    test_gives_up_politely_if_the_clash_never_clears()
    print("test_github_mock: all checks passed")
