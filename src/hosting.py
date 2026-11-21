"""Instagram API は「公開 URL の画像」しか受け付けないため、加工後の画像を GitHub に置いて URL を作る。

既定では、このリポジトリ（公開リポジトリ）の `media` ブランチに保存します。
リポジトリを非公開にしたい場合は、画像置き場用の公開リポジトリを別に作り、
Secrets に MEDIA_REPO（owner/name）と MEDIA_REPO_TOKEN（そのリポジトリに書ける PAT）を入れてください。
"""
from __future__ import annotations

import base64
import time

import requests

from common import env

API = "https://api.github.com"


def _cfg():
    repo = env("MEDIA_REPO") or env("GITHUB_REPOSITORY", required=True)
    token = env("MEDIA_REPO_TOKEN") or env("GITHUB_TOKEN", required=True)
    branch = env("MEDIA_BRANCH", "media")
    return repo, branch, {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}


def _ensure_branch(repo, branch, h):
    r = requests.get(f"{API}/repos/{repo}/branches/{branch}", headers=h, timeout=30)
    if r.status_code == 200:
        return
    info = requests.get(f"{API}/repos/{repo}", headers=h, timeout=30).json()
    sha = requests.get(f"{API}/repos/{repo}/git/ref/heads/{info['default_branch']}",
                       headers=h, timeout=30).json()["object"]["sha"]
    requests.post(f"{API}/repos/{repo}/git/refs", headers=h, timeout=30,
                  json={"ref": f"refs/heads/{branch}", "sha": sha}).raise_for_status()


def upload(path: str, data: bytes) -> str:
    repo, branch, h = _cfg()
    _ensure_branch(repo, branch, h)
    url = f"{API}/repos/{repo}/contents/{path}"
    body = {"message": f"media: {path}", "content": base64.b64encode(data).decode(), "branch": branch}
    old = requests.get(url, headers=h, params={"ref": branch}, timeout=30)
    if old.status_code == 200:
        body["sha"] = old.json()["sha"]
    requests.put(url, headers=h, json=body, timeout=60).raise_for_status()

    public = f"https://raw.githubusercontent.com/{repo}/{branch}/{path}"
    for _ in range(20):                      # CDN に反映されるまで待つ
        if requests.head(public, timeout=15).status_code == 200:
            return public
        time.sleep(3)
    raise RuntimeError(f"公開URLにアクセスできません（リポジトリが非公開？）: {public}")
