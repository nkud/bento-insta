"""Drive の新しいお弁当写真を見つけて、加工して Instagram に投稿する。

使い方:
  python src/post.py                 # 本番（GitHub Actions から呼ばれる）
  python src/post.py --dry-run       # 投稿せず out/ に画像とキャプションを書き出す
  python src/post.py --local a.jpg   # Drive を使わず手元の写真で試す（--dry-run と併用推奨）
"""
from __future__ import annotations

import argparse
import datetime as dt
import io
import pathlib
import random
import sys
import traceback
import zlib

from PIL import Image, ImageOps

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import annotate  # noqa: E402
import caption  # noqa: E402
import strategy as strat  # noqa: E402
import vision  # noqa: E402
from common import OUT, POSTS_FILE, env, load_config, load_json, now_jst, save_json  # noqa: E402

try:
    from pillow_heif import register_heif_opener
    register_heif_opener()                       # iPhone の HEIC に対応
except ImportError:
    pass


def open_image(raw: bytes) -> Image.Image:
    img = Image.open(io.BytesIO(raw))
    img = ImageOps.exif_transpose(img)           # スマホ写真の向きを補正
    return img.convert("RGB")


def to_jpeg(img: Image.Image, q: int) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=q, optimize=True, progressive=True)
    return buf.getvalue()


def make_post(img: Image.Image, memo: str, cfg: dict, strategy: dict, seed: int) -> dict:
    """解析 → 切り出し → 手書き風画像 → キャプション。投稿前までの全部。"""
    analysis = vision.analyze(img, memo, cfg["claude"]["model"])
    if not analysis.get("is_bento", True):
        raise ValueError("お弁当の写真ではないと判定されました")
    W, H = cfg["image"]["width"], cfg["image"]["height"]
    photo, to_px = annotate.crop_portrait(img, analysis["bento_bbox"], W, H)
    b = analysis["bento_bbox"]
    bx0, by0 = to_px(b["x0"], b["y0"])
    bx1, by1 = to_px(b["x1"], b["y1"])
    dishes_px = [{**d, "px": to_px(d["x"], d["y"])[0], "py": to_px(d["x"], d["y"])[1]}
                 for d in analysis["dishes"]]
    variant = strat.choose_variant(cfg, strategy, random.Random(seed))
    drawn = annotate.render(photo, analysis["title"], dishes_px, (bx0, by0, bx1, by1),
                            variant, date=now_jst().date(), seed=seed)
    text = caption.build(analysis, memo, variant, strategy, cfg)
    q = cfg["image"]["jpeg_quality"]
    return {"analysis": analysis, "variant": variant, "caption": text,
            "img1": to_jpeg(photo, q), "img2": to_jpeg(drawn, q)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--local", help="手元の画像ファイルで試す")
    ap.add_argument("--memo", default="", help="--local 用のメモ")
    ap.add_argument("--force", action="store_true", help="投稿時間帯・間隔の制限を無視")
    args = ap.parse_args()
    dry = args.dry_run or env("DRY_RUN") == "1"

    cfg = load_config()
    strategy = strat.load()
    posts = load_json(POSTS_FILE, [])
    now = now_jst()

    # ---- ローカル試行
    if args.local:
        raw = pathlib.Path(args.local).read_bytes()
        p = make_post(open_image(raw), args.memo, cfg, strategy, seed=int(now.timestamp()))
        _write_out("local", p)
        return

    # ---- 投稿してよいタイミングか
    force = args.force or env("FORCE") == "1"
    hours = strat.allowed_hours(cfg, strategy)
    if not force and not dry and now.hour not in hours:
        print(f"投稿時間外です（現在 {now.hour}時 / 許可 {hours}）。スキップ")
        return
    last = max((p["posted_at"] for p in posts if p.get("status") == "posted"), default=None)
    gap_h = cfg["posting"]["min_interval_hours"]
    if not force and not dry and last and now - dt.datetime.fromisoformat(last) < dt.timedelta(hours=gap_h):
        print(f"前回投稿から {gap_h} 時間経っていません。スキップ")
        return

    # ---- Drive から新しい写真を探す
    import drive
    folder = env("DRIVE_FOLDER_ID") or cfg["drive"]["folder_id"]
    if not folder:
        raise SystemExit("DRIVE_FOLDER_ID が未設定です")
    svc = drive.service()
    by_id = {p["drive_file_id"]: p for p in posts}
    max_retry = cfg["posting"]["max_retries"]
    todo = [f for f in drive.list_images(svc, folder)
            if f["id"] not in by_id
            or (by_id[f["id"]].get("status") == "failed" and by_id[f["id"]].get("attempts", 0) < max_retry)]
    print(f"未投稿の写真: {len(todo)} 枚")

    for f in todo[: cfg["posting"]["max_posts_per_run"]]:
        rec = by_id.get(f["id"]) or {"drive_file_id": f["id"], "drive_name": f["name"],
                                     "drive_created": f["createdTime"], "attempts": 0}
        is_new = rec not in posts
        if is_new:
            posts.append(rec)
        rec["attempts"] = rec.get("attempts", 0) + 1
        try:
            memo = drive.find_memo(svc, folder, f)
            img = open_image(drive.download(svc, f["id"]))
            p = make_post(img, memo, cfg, strategy, seed=zlib.crc32(f["id"].encode()))
            rec.update({"memo": memo, "title": p["analysis"]["title"],
                        "analysis": p["analysis"], "variant": p["variant"], "caption": p["caption"],
                        "hashtags": caption.hashtags_of(p["caption"])})
            if dry:
                _write_out(f["id"], p)
                if is_new:
                    posts.remove(rec)    # ドライランは記録しない
                continue

            import hosting
            from instagram import IG
            stamp = now.strftime("%Y%m%d-%H%M")
            url1 = hosting.upload(f"media/{stamp}-{f['id'][:8]}-1.jpg", p["img1"])
            url2 = hosting.upload(f"media/{stamp}-{f['id'][:8]}-2.jpg", p["img2"])
            ig = IG(cfg["instagram"]["graph_version"], cfg["instagram"].get("host", "graph.facebook.com"))
            info = ig.publish_carousel([url1, url2], p["caption"])
            rec.update({"status": "posted", "ig_media_id": info["id"], "permalink": info.get("permalink"),
                        "posted_at": now.isoformat(), "image_urls": [url1, url2], "error": None})
            try:
                rec["followers_at_post"] = ig.account().get("followers_count")
            except RuntimeError:
                pass
            print(f"投稿しました: {info.get('permalink')}")
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            rec.update({"status": "failed", "error": str(e)[:500]})
        finally:
            save_json(POSTS_FILE, posts)   # 1枚ごとに保存して二重投稿を防ぐ


def _write_out(name: str, p: dict):
    OUT.mkdir(exist_ok=True)
    (OUT / f"{name}-1.jpg").write_bytes(p["img1"])
    (OUT / f"{name}-2.jpg").write_bytes(p["img2"])
    (OUT / f"{name}-caption.txt").write_text(p["caption"], encoding="utf-8")
    print(f"out/{name}-*.jpg と caption を書き出しました\n---\n{p['caption']}\n---\n変化軸: {p['variant']}")


if __name__ == "__main__":
    main()
