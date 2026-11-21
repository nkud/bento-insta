"""お弁当写真 → 下書き（GitHub Issue）→ 承認されたら Instagram に投稿。

定期実行（post.yml）でやること:
  1. 予約時刻を過ぎた下書きを投稿する
  2. Drive の新しい写真から下書き Issue を作る（ここでは投稿しない）

承認・修正はIssueのコメントで行う（approve.py）:
  /post            今すぐ投稿
  /post 19:30      時刻を指定して予約（「明日 8:00」も可）
  /redo [メモ]     作り直し（メモを足すとキャプションに反映）
  /skip            投稿しない

手元で試す:
  python src/post.py --local a.jpg --memo "..."   # out/ に画像とキャプションを書き出す
"""
from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import pathlib
import random
import re
import sys
import traceback
import zlib

from PIL import Image, ImageOps

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import annotate  # noqa: E402
import caption  # noqa: E402
import color  # noqa: E402
import memo  # noqa: E402
import strategy as strat  # noqa: E402
import vision  # noqa: E402
from common import OUT, POSTS_FILE, env, load_config, load_json, now_jst, save_json  # noqa: E402

try:
    from pillow_heif import register_heif_opener
    register_heif_opener()                       # iPhone の HEIC に対応
except ImportError:
    pass

DRAFT_LABEL = "下書き"
SCHEDULED_LABEL = "予約済み"


# ------------------------------------------------------------------ 画像とキャプションを作る
def open_image(raw: bytes) -> Image.Image:
    img = Image.open(io.BytesIO(raw))
    img = ImageOps.exif_transpose(img)           # スマホ写真の向きを補正
    return img.convert("RGB")


def to_jpeg(img: Image.Image, q: int) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=q, optimize=True, progressive=True)
    return buf.getvalue()


def make_post(img: Image.Image, note: str, cfg: dict, strategy: dict, seed: int) -> dict:
    """解析 → 切り出し・色補正 → 手書き風画像 → 作り置きメモ → キャプション。"""
    analysis = vision.analyze(img, note, cfg["claude"]["model"])
    if not analysis.get("is_bento", True):
        raise ValueError("お弁当の写真ではないと判定されました")
    W, H = cfg["image"]["width"], cfg["image"]["height"]
    photo, to_px = annotate.crop_portrait(img, analysis["bento_bbox"], W, H)
    photo = color.enhance(photo, cfg["image"].get("color"))
    b = analysis["bento_bbox"]
    bx0, by0 = to_px(b["x0"], b["y0"])
    bx1, by1 = to_px(b["x1"], b["y1"])
    dishes_px = [{**d, "px": to_px(d["x"], d["y"])[0], "py": to_px(d["x"], d["y"])[1]}
                 for d in analysis["dishes"]]
    variant = strat.choose_variant(cfg, strategy, random.Random(seed))
    drawn = annotate.render(photo, analysis["title"], dishes_px, (bx0, by0, bx1, by1),
                            variant, date=now_jst().date(), seed=seed)
    text = caption.build(analysis, note, variant, strategy, cfg)
    q = cfg["image"]["jpeg_quality"]
    images = [to_jpeg(photo, q), to_jpeg(drawn, q)]
    if cfg["image"].get("memo_slide") and analysis.get("prep"):
        images.append(to_jpeg(memo.render(photo, analysis["title"], analysis["prep"], (W, H), seed=seed), q))
    return {"analysis": analysis, "variant": variant, "caption": text, "images": images}


# ------------------------------------------------------------------ 下書き Issue
def issue_body(rec: dict, strategy: dict) -> str:
    urls = rec["image_urls"]
    heads = ["1枚目", "2枚目", "3枚目（作り置きメモ）"][: len(urls)]
    hint = ""
    hours = (strategy.get("insights") or {}).get("best_posting_hours") or []
    if strategy.get("learning_active") and hours:
        hint = f"\n> 📈 これまでのデータでは **{'・'.join(f'{h}時' for h in sorted(hours))}** ごろの投稿が伸びやすいです\n"
    memo_line = f"\n**メモ:** {rec['memo']}\n" if rec.get("memo") else ""
    v = rec.get("variant", {})
    return f"""## 🍱 {rec['title']}

| {" | ".join(heads)} |
|{"---|" * len(urls)}
| {" | ".join(f'<img src="{u}" width="260">' for u in urls)} |
{memo_line}
### ✏️ キャプション
下の枠の中を編集すると、そのまま投稿されます（右上の「…」→ Edit）。

<!-- caption:start -->
```text
{rec['caption']}
```
<!-- caption:end -->

### 💬 コメントで操作
| コメント | 動き |
|---|---|
| `/post` | 今すぐ投稿 |
| `/post 19:30` | 時刻を指定して予約（`/post 明日 8:00` も可） |
| `/redo` | 作り直し。`/redo 肉巻きは豚バラ、お隣さんからの差し入れ` のようにメモも足せる |
| `/skip` | 投稿しない |
{hint}
<sub>パターン: タイトル={v.get('title_style')} / フォント={v.get('font')} / コメント={v.get('caption_length')} / タグ数={v.get('hashtag_count')}</sub>
<!-- drive_file_id: {rec['drive_file_id']} -->
"""


def parse_caption(body: str) -> str | None:
    m = re.search(r"<!-- caption:start -->\s*```[a-z]*\n(.*?)\n```\s*<!-- caption:end -->", body or "", re.S)
    return m.group(1).strip() if m else None


def build_draft(svc, folder: str, rec: dict, cfg: dict, strategy: dict, extra_memo: str = "") -> None:
    """Drive の写真から画像・キャプションを作り、公開 URL に置いて rec に書き込む。"""
    import drive
    import hosting
    f = {"id": rec["drive_file_id"], "name": rec["drive_name"], "description": rec.get("drive_description")}
    note = drive.find_memo(svc, folder, f)
    if extra_memo:
        note = (note + "\n" + extra_memo).strip()
    img = open_image(drive.download(svc, f["id"]))
    rec["attempts"] = rec.get("attempts", 0) + 1
    seed = zlib.crc32(f"{f['id']}-{rec['attempts']}".encode())
    p = make_post(img, note, cfg, strategy, seed=seed)
    stamp = now_jst().strftime("%Y%m%d-%H%M%S")
    urls = [hosting.upload(f"media/{stamp}-{f['id'][:8]}-{i}.jpg", b) for i, b in enumerate(p["images"], 1)]
    rec.update({"memo": note, "title": p["analysis"]["title"], "analysis": p["analysis"],
                "variant": p["variant"], "caption": p["caption"], "image_urls": urls, "error": None})


# ------------------------------------------------------------------ 投稿
def publish(rec: dict, cfg: dict) -> None:
    """Issue 上の（編集済みかもしれない）キャプションで Instagram に投稿する。"""
    import github_api as gh
    from instagram import IG
    issue = gh.get_issue(rec["issue_number"])
    text = parse_caption(issue.get("body", "")) or rec["caption"]
    ig = IG(cfg["instagram"]["graph_version"], cfg["instagram"].get("host", "graph.facebook.com"))
    info = ig.publish_carousel(rec["image_urls"], text)
    rec.update({"status": "posted", "ig_media_id": info["id"], "permalink": info.get("permalink"),
                "posted_at": now_jst().isoformat(), "caption_edited": text != rec["caption"],
                "caption": text, "hashtags": caption.hashtags_of(text), "scheduled_at": None})
    try:
        rec["followers_at_post"] = ig.account().get("followers_count")
    except RuntimeError:
        pass
    gh.comment(rec["issue_number"], f"✅ 投稿しました！ {rec.get('permalink') or ''}")
    gh.set_labels(rec["issue_number"], ["投稿済み"])
    gh.update_issue(rec["issue_number"], state="closed")


def publish_due(posts: list, cfg: dict) -> None:
    import github_api as gh
    now = now_jst()
    for rec in posts:
        if rec.get("status") == "scheduled" and rec.get("scheduled_at") \
                and dt.datetime.fromisoformat(rec["scheduled_at"]) <= now:
            try:
                publish(rec, cfg)
                print(f"予約投稿しました: {rec.get('permalink')}")
            except Exception as e:  # noqa: BLE001
                traceback.print_exc()
                rec.update({"status": "draft", "error": str(e)[:500], "scheduled_at": None})
                gh.comment(rec["issue_number"], f"⚠️ 予約投稿に失敗しました。もう一度 `/post` してください。\n```\n{e}\n```")
                gh.set_labels(rec["issue_number"], [DRAFT_LABEL])
            save_json(POSTS_FILE, posts)


def prepare_drafts(posts: list, cfg: dict, strategy: dict) -> int:
    import drive
    import github_api as gh
    folder = env("DRIVE_FOLDER_ID") or cfg["drive"]["folder_id"]
    if not folder:
        raise SystemExit("DRIVE_FOLDER_ID が未設定です")
    svc = drive.service()
    by_id = {p["drive_file_id"]: p for p in posts}
    max_retry = cfg["posting"]["max_retries"]
    todo = [f for f in drive.list_images(svc, folder)
            if f["id"] not in by_id
            or (by_id[f["id"]].get("status") == "failed" and by_id[f["id"]].get("attempts", 0) < max_retry)]
    print(f"新しい写真: {len(todo)} 枚")
    if not todo and not by_id:
        sa = json.loads(env("GOOGLE_SERVICE_ACCOUNT_JSON"))["client_email"]
        print(f"※ 写真が見つかりません。Drive フォルダ（ID: {folder}）が {sa} に共有されているか確認してください")
    failed = 0
    for f in todo[: cfg["posting"]["max_drafts_per_run"]]:
        rec = by_id.get(f["id"]) or {"drive_file_id": f["id"], "drive_name": f["name"],
                                     "drive_created": f["createdTime"], "attempts": 0}
        rec["drive_description"] = f.get("description")
        if rec not in posts:
            posts.append(rec)
        try:
            build_draft(svc, folder, rec, cfg, strategy)
            rec["issue_number"] = gh.create_issue(f"🍱 下書き：{rec['title']}", issue_body(rec, strategy),
                                                  [DRAFT_LABEL])
            rec["status"] = "draft"
            print(f"下書きを作りました: #{rec['issue_number']} {rec['title']}")
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            rec.update({"status": "failed", "error": str(e)[:500]})
            failed += 1
        finally:
            save_json(POSTS_FILE, posts)
    return failed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--local", help="手元の画像ファイルで試す（out/ に書き出すだけ）")
    ap.add_argument("--memo", default="", help="--local 用のメモ")
    args = ap.parse_args()
    cfg = load_config()
    strategy = strat.load()

    if args.local:
        raw = pathlib.Path(args.local).read_bytes()
        p = make_post(open_image(raw), args.memo, cfg, strategy, seed=int(now_jst().timestamp()))
        OUT.mkdir(exist_ok=True)
        for i, b in enumerate(p["images"], 1):
            (OUT / f"local-{i}.jpg").write_bytes(b)
        (OUT / "local-caption.txt").write_text(p["caption"], encoding="utf-8")
        print(f"out/ に書き出しました\n---\n{p['caption']}\n---\nパターン: {p['variant']}")
        return

    posts = load_json(POSTS_FILE, [])
    publish_due(posts, cfg)
    failed = prepare_drafts(posts, cfg, strategy)
    if failed:
        sys.exit(f"{failed} 枚の処理に失敗しました（上のエラーを確認）")


if __name__ == "__main__":
    main()
