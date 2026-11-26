"""Instagram API でカルーセル投稿・指標取得（統合ログイン / Instagram ログイン両対応）。"""
from __future__ import annotations

import time

import requests

from common import env


class IG:
    def __init__(self, version: str = "v26.0", host: str = "graph.facebook.com"):
        # 統合ログインのシステムユーザートークン（EAA...）は graph.facebook.com、
        # Instagram ログインのトークン（IGAA...）は graph.instagram.com を使う
        self.token = env("IG_ACCESS_TOKEN", required=True)
        self.base = f"https://{host}/{version}"
        self.user_id = env("IG_USER_ID")
        if not self.user_id:
            if host != "graph.instagram.com":
                raise SystemExit("IG_USER_ID（Instagram のユーザーID）を Secrets に登録してください")
            self.user_id = self.get("me", fields="user_id")["user_id"]

    # ---- 低レベル
    def _check(self, r: requests.Response) -> dict:
        data = r.json()
        if r.status_code >= 400 or "error" in data:
            raise RuntimeError(f"Instagram API エラー: {data.get('error', data)}")
        return data

    def get(self, path: str, **params) -> dict:
        params["access_token"] = self.token
        return self._check(requests.get(f"{self.base}/{path}", params=params, timeout=60))

    def post(self, path: str, **params) -> dict:
        params["access_token"] = self.token
        return self._check(requests.post(f"{self.base}/{path}", data=params, timeout=120))

    # ---- 投稿
    def _wait(self, container_id: str, timeout: int = 300):
        t0 = time.time()
        while time.time() - t0 < timeout:
            st = self.get(container_id, fields="status_code,status")
            if st.get("status_code") == "FINISHED":
                return
            if st.get("status_code") in ("ERROR", "EXPIRED"):
                raise RuntimeError(f"コンテナ処理失敗: {st}")
            time.sleep(5)
        raise TimeoutError("コンテナ処理がタイムアウトしました")

    def publish_carousel(self, image_urls: list[str], caption: str) -> dict:
        children = []
        for url in image_urls:
            c = self.post(f"{self.user_id}/media", image_url=url, is_carousel_item="true")
            self._wait(c["id"])
            children.append(c["id"])
        parent = self.post(f"{self.user_id}/media", media_type="CAROUSEL",
                           children=",".join(children), caption=caption)
        self._wait(parent["id"])
        media = self.post(f"{self.user_id}/media_publish", creation_id=parent["id"])
        info = self.get(media["id"], fields="id,permalink,timestamp")
        return info

    # ---- 指標
    def media_fields(self, media_id: str) -> dict:
        return self.get(media_id, fields="like_count,comments_count,timestamp,permalink")

    def media_insights(self, media_id: str,
                       metrics=("reach", "views", "saved", "shares", "likes", "comments",
                                "total_interactions")) -> dict:
        def fetch(ms):
            data = self.get(f"{media_id}/insights", metric=",".join(ms))
            return {d["name"]: d["values"][0]["value"] for d in data.get("data", [])}
        try:
            return fetch(metrics)
        except RuntimeError:          # 一部の指標が非対応のときは1つずつ
            out, errs = {}, {}
            for m in metrics:
                try:
                    out.update(fetch([m]))
                except RuntimeError as e:
                    errs[m] = str(e)[:200]
            if errs:   # 取れなかった指標は理由をログに残す（黙って null にしない）
                print(f"インサイト取得できず {media_id}: " + "; ".join(f"{k}: {v}" for k, v in errs.items()))
            return out

    def account(self) -> dict:
        return self.get(self.user_id, fields="username,followers_count,media_count")
