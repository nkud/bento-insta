"""claude.ai のダッシュボード（Artifact）に載せるデータを JSON で書き出す。

  python src/export_artifact.py            # out/artifact/ に posts.json・followers.json・insights.json

定期実行（Claude の scheduled task）がこれを動かして、ダッシュボードのファイルを差し替える。
サムネイルは media ブランチの画像を小さくして data: URL で埋め込む（Artifact は外部の画像を読めないため）。
"""
from __future__ import annotations

import base64
import datetime as dt
import io
import json
import pathlib
import subprocess
import sys

from PIL import Image

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from analyze import WEEK, _band, _num  # noqa: E402
from common import ACCOUNT_FILE, POSTS_FILE, ROOT, STRATEGY_FILE, load_config, load_json, now_jst  # noqa: E402

REPO = "nkud/bento-insta"
OUT = ROOT / "out" / "artifact"
THUMBS = 30                      # サムネイルを埋め込む件数（新しい順）


def _media_blob(url: str) -> bytes | None:
    """raw.githubusercontent.com/<repo>/media/<path> の画像を git から読む。"""
    if not url or "/media/" not in url:
        return None
    path = url.split(f"/{REPO}/media/", 1)[-1]
    try:
        return subprocess.run(["git", "show", f"FETCH_HEAD:{path}"], cwd=ROOT, capture_output=True,
                              check=True).stdout
    except subprocess.CalledProcessError:
        return None


def _thumb(url: str) -> str:
    raw = _media_blob(url)
    if not raw:
        return ""
    im = Image.open(io.BytesIO(raw)).convert("RGB")
    im.thumbnail((200, 250))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=70, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def post_rows(posts: list, cfg: dict) -> list[dict]:
    now = now_jst()
    mature_h = cfg["analysis"]["mature_hours"]
    order = sorted(posts, key=lambda p: p.get("posted_at") or p.get("scheduled_at") or p.get("drive_created") or "",
                   reverse=True)
    rows = []
    for k, p in enumerate(order):
        m = (p.get("snapshots") or {}).get("72") or p.get("metrics") or {}
        def _val(x):   # 取れなかった指標は 0 ではなく空（null）のまま渡す
            v = m.get(x)
            return float(v) if isinstance(v, (int, float)) else None
        reach, likes, saved, shares, com = (_val(x) for x in ("reach", "likes", "saved", "shares", "comments"))
        posted = dt.datetime.fromisoformat(p["posted_at"]) if p.get("posted_at") else None
        age_h = (now - posted).total_seconds() / 3600 if posted else None
        a = p.get("analysis") or {}
        v = p.get("variant") or {}
        n = len(a.get("dishes", []))
        imgs = p.get("image_urls") or []
        rows.append({
            "id": p["drive_file_id"],
            "status": p.get("status") or "",
            "title": p.get("title") or p.get("drive_name") or "",
            "posted_at": posted.strftime("%Y-%m-%d %H:%M") if posted else "",
            "scheduled_at": dt.datetime.fromisoformat(p["scheduled_at"]).strftime("%Y-%m-%d %H:%M")
            if p.get("scheduled_at") else "",
            "has_metrics": bool(p.get("metrics")),
            "mature": bool(p.get("metrics")) and age_h is not None and age_h >= mature_h,
            "likes": likes, "saved": saved, "comments": com, "shares": shares, "reach": reach,
            "score": round(((likes or 0) + 2 * (saved or 0) + 2 * (shares or 0) + 3 * (com or 0)) / reach * 100, 2)
            if reach else None,
            "ig_missing": bool(p.get("ig_missing")),
            "title_style": str(v.get("title_style", "")),
            "pen": str(v.get("pen", "")),
            "caption_length": str(v.get("caption_length", "")),
            "hashtag_count": str(v.get("hashtag_count", "")),
            "main": a.get("main", ""),
            "tone": a.get("tone", ""),
            "dish_count": "" if not n else ("3品以下" if n <= 3 else ("4〜5品" if n <= 5 else "6品以上")),
            "weekday": WEEK[posted.weekday()] if posted else "",
            "hour": _band(posted.hour) if posted else "",
            "hashtags": " ".join(p.get("hashtags") or []),
            "permalink": p.get("permalink") or "",
            "issue_url": f"https://github.com/{REPO}/issues/{p['issue_number']}" if p.get("issue_number") else "",
            "error": (p.get("error") or "")[:200] if p.get("status") == "failed" else "",
            "thumb": _thumb(imgs[1] if len(imgs) > 1 else (imgs[0] if imgs else "")) if k < THUMBS else "",
        })
    return rows


def follower_rows(hist: list) -> list[dict]:
    by_day = {}
    for h in hist:
        if h.get("followers") is not None:
            by_day[dt.datetime.fromisoformat(h["at"]).strftime("%Y-%m-%d")] = h["followers"]
    return [{"date": d, "followers": f} for d, f in sorted(by_day.items())]


def insight_rows(strategy: dict) -> list[dict]:
    ins = strategy.get("insights") or {}
    rows = []
    if ins.get("summary"):
        rows.append({"kind": "summary", "text": ins["summary"]})
    for kind, key in (("finding", "findings"), ("tip", "caption_tips"), ("idea", "content_ideas"),
                      ("good_tag", "recommended_hashtags"), ("bad_tag", "avoid_hashtags")):
        rows += [{"kind": kind, "text": str(t)} for t in ins.get(key, [])]
    rows += [{"kind": "hour", "text": f"{h}時"} for h in sorted(ins.get("best_posting_hours") or [])]
    if strategy.get("updated_at"):
        rows.append({"kind": "updated", "text": strategy["updated_at"][:16].replace("T", " ")})
    return rows


def main():
    cfg = load_config()
    subprocess.run(["git", "fetch", "-q", "--depth=1", "origin", "media"], cwd=ROOT, check=False)
    OUT.mkdir(parents=True, exist_ok=True)
    files = {
        "posts.json": post_rows(load_json(POSTS_FILE, []), cfg),
        "followers.json": follower_rows(load_json(ACCOUNT_FILE, [])),
        "insights.json": insight_rows(load_json(STRATEGY_FILE, {})),
    }
    for name, rows in files.items():
        (OUT / name).write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        print(f"out/artifact/{name}: {len(rows)} 行")


if __name__ == "__main__":
    main()
