"""GitHub Issue を「下書き」として使うための最小限の API ラッパー。"""
from __future__ import annotations

import requests

from common import env

API = "https://api.github.com"


def _h():
    return {"Authorization": f"Bearer {env('GITHUB_TOKEN', required=True)}",
            "Accept": "application/vnd.github+json"}


def _repo():
    return env("GITHUB_REPOSITORY", required=True)


def _req(method: str, path: str, **kw) -> dict:
    r = requests.request(method, f"{API}/repos/{_repo()}{path}", headers=_h(), timeout=30, **kw)
    r.raise_for_status()
    return r.json() if r.content else {}


def create_issue(title: str, body: str, labels: list[str]) -> int:
    return _req("POST", "/issues", json={"title": title, "body": body, "labels": labels})["number"]


def get_issue(number: int) -> dict:
    return _req("GET", f"/issues/{number}")


def update_issue(number: int, **fields) -> None:
    _req("PATCH", f"/issues/{number}", json=fields)


def comment(number: int, body: str) -> None:
    _req("POST", f"/issues/{number}/comments", json={"body": body})


def set_labels(number: int, labels: list[str]) -> None:
    _req("PUT", f"/issues/{number}/labels", json={"labels": labels})


def list_comments(number: int) -> list[dict]:
    out, page = [], 1
    while True:
        r = requests.get(f"{API}/repos/{_repo()}/issues/{number}/comments", headers=_h(),
                         params={"per_page": 100, "page": page}, timeout=30)
        r.raise_for_status()
        batch = r.json()
        out += batch
        if len(batch) < 100:
            return out
        page += 1
