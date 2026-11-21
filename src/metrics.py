"""投稿済みのいいね数・保存数・リーチなどを集める（毎日実行）。"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from common import ACCOUNT_FILE, POSTS_FILE, load_config, load_json, now_jst, save_json  # noqa: E402
from instagram import IG  # noqa: E402
import datetime as dt  # noqa: E402

CHECKPOINTS = (24, 72, 168)    # 1日後・3日後・7日後のスナップショットを残す
STOP_AFTER_DAYS = 45           # これより古い投稿は更新しない


def main():
    cfg = load_config()
    ig = IG(cfg["instagram"]["graph_version"])
    posts = load_json(POSTS_FILE, [])
    now = now_jst()

    acc = ig.account()
    hist = load_json(ACCOUNT_FILE, [])
    hist.append({"at": now.isoformat(), "followers": acc.get("followers_count"),
                 "media_count": acc.get("media_count")})
    save_json(ACCOUNT_FILE, hist[-400:])
    print(f"@{acc.get('username')} フォロワー {acc.get('followers_count')}")

    for p in posts:
        if p.get("status") != "posted":
            continue
        age_h = (now - dt.datetime.fromisoformat(p["posted_at"])).total_seconds() / 3600
        if age_h > STOP_AFTER_DAYS * 24 and p.get("metrics"):
            continue
        try:
            f = ig.media_fields(p["ig_media_id"])
            ins = ig.media_insights(p["ig_media_id"])
        except RuntimeError as e:
            print(f"取得失敗 {p['ig_media_id']}: {e}")
            continue
        m = {"likes": ins.get("likes", f.get("like_count")),
             "comments": ins.get("comments", f.get("comments_count")),
             "saved": ins.get("saved"), "shares": ins.get("shares"),
             "reach": ins.get("reach"), "views": ins.get("views"),
             "total_interactions": ins.get("total_interactions"),
             "age_hours": round(age_h, 1), "collected_at": now.isoformat()}
        p["metrics"] = m
        if p.get("followers_at_post") is None:
            p["followers_at_post"] = acc.get("followers_count")
        snaps = p.setdefault("snapshots", {})
        for cp in CHECKPOINTS:
            if age_h >= cp and str(cp) not in snaps:
                snaps[str(cp)] = m
    save_json(POSTS_FILE, posts)
    print(f"{sum(1 for p in posts if p.get('metrics'))} 件の指標を更新")


if __name__ == "__main__":
    main()
