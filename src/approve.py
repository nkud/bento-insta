"""下書き Issue へのコメント（/post /redo /skip）を処理する。approve.yml から呼ばれる。"""
from __future__ import annotations

import datetime as dt
import pathlib
import re
import sys
import traceback
import unicodedata

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import github_api as gh  # noqa: E402
import post  # noqa: E402
import strategy as strat  # noqa: E402
from common import POSTS_FILE, env, load_config, load_json, now_jst, save_json  # noqa: E402


def parse_time(arg: str) -> dt.datetime | None:
    """「19:30」「1930」「19時」「19時30分」「明日 8:00」を JST の日時にする。過ぎていれば翌日。"""
    s = unicodedata.normalize("NFKC", arg).replace(" ", "")
    tomorrow = s.startswith("明日")
    s = s.removeprefix("明日").removeprefix("今日")
    m = re.fullmatch(r"(\d{1,2})(?::|時)?(\d{2})?分?", s)
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2) or 0)
    if h > 23 or mi > 59:
        return None
    now = now_jst()
    t = now.replace(hour=h, minute=mi, second=0, microsecond=0)
    if tomorrow or t <= now:
        t += dt.timedelta(days=1)
    return t


def main():
    number = int(env("ISSUE_NUMBER", required=True))
    text = unicodedata.normalize("NFKC", env("COMMENT_BODY", "")).strip()
    m = re.match(r"^/(post|redo|skip)\b\s*(.*)$", text, re.S)
    if not m:
        print("コマンドではないコメントなので何もしません")
        return
    cmd, arg = m.group(1), m.group(2).strip()

    cfg = load_config()
    posts = load_json(POSTS_FILE, [])
    rec = next((p for p in posts if p.get("issue_number") == number), None)
    if not rec:
        gh.comment(number, "⚠️ この Issue に対応する下書きが見つかりませんでした")
        return
    if rec.get("status") == "posted":
        gh.comment(number, f"これはもう投稿済みです: {rec.get('permalink')}")
        return

    try:
        if cmd == "skip":
            rec.update({"status": "skipped", "scheduled_at": None})
            gh.comment(number, "🙅 この写真は投稿しないことにしました")
            gh.set_labels(number, ["見送り"])
            gh.update_issue(number, state="closed")

        elif cmd == "post" and not arg:
            post.publish(rec, cfg)

        elif cmd == "post":
            t = parse_time(arg)
            if not t:
                gh.comment(number, f"⚠️ 時刻が読み取れませんでした: `{arg}`（例: `/post 19:30`、`/post 明日 8:00`）")
                return
            rec.update({"status": "scheduled", "scheduled_at": t.isoformat()})
            gh.comment(number, f"⏰ **{t:%m/%d %H:%M}** に投稿を予約しました（チェックは15分おきなので、最大15分ほど後になります）。\n"
                               "それまでにキャプションを編集すれば、編集後の内容で投稿されます。取り消しは `/skip`、すぐ出すなら `/post`。")
            gh.set_labels(number, [post.SCHEDULED_LABEL])

        elif cmd == "redo":
            import drive
            folder = env("DRIVE_FOLDER_ID") or cfg["drive"]["folder_id"]
            post.build_draft(drive.service(), folder, rec, cfg, strat.load(), extra_memo=arg)
            rec.update({"status": "draft", "scheduled_at": None})
            gh.update_issue(number, title=f"🍱 下書き：{rec['title']}", body=post.issue_body(rec, strat.load()))
            gh.set_labels(number, [post.DRAFT_LABEL])
            gh.comment(number, "🔁 作り直しました。上の画像とキャプションを確認してください")
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        rec["error"] = str(e)[:500]
        gh.comment(number, f"⚠️ `/{cmd}` に失敗しました。\n```\n{e}\n```")
        raise
    finally:
        save_json(POSTS_FILE, posts)


if __name__ == "__main__":
    main()
