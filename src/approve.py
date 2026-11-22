"""下書き Issue へのコメントを処理する。approve.yml から呼ばれる。

ふつうの言葉で書けば Claude が意図を読み取って動く:
  「いいね、出して」              → 今すぐ投稿
  「今夜8時に」「明日の朝7時半」    → 予約
  「最後の一文いらない」「#唐揚げを足して」→ キャプションを直す（直して予約、も一度にできる）
  「エビじゃなくてホタテ、作り直して」「文字を太く」→ 画像から作り直す
  「これはやめとく」              → 見送り
従来の /post /post 19:30 /redo /skip もそのまま使える。
"""
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
from common import JST, POSTS_FILE, env, load_config, load_json, now_jst, save_json  # noqa: E402


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


# ------------------------------------------------------------------ ことばの解釈
TOOL = {
    "name": "decide",
    "description": "コメントの意図を読み取り、下書きに対してやることを決める",
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {"type": "string",
                       "enum": ["post_now", "schedule", "edit_image", "redo", "skip", "caption_only", "none"],
                       "description": "post_now=今すぐ投稿 / schedule=時刻を決めて予約 / edit_image=2枚目の手書き画像を直す（写真の解析はそのまま） / "
                                      "redo=写真から解析し直して全部作り直す / skip=投稿しない / caption_only=キャプションを直すだけ / none=指示ではない・意図が曖昧"},
            "image_title": {"type": "string", "description": "edit_image で2枚目のお弁当名（タイトル）を変えるとき、新しいお弁当名（例: オムライス弁当）"},
            "dish_edits": {"type": "array", "description": "edit_image で2枚目のおかずを直すとき。変えるおかずだけ",
                           "items": {"type": "object", "properties": {
                               "index": {"type": "integer", "description": "下の『2枚目のおかず』の番号"},
                               "name": {"type": "string", "description": "新しいおかず名（変えるときだけ）"},
                               "note": {"type": "string", "description": "補足（かっこ書き）。消すときは空文字"},
                               "place": {"type": "string", "enum": ["T", "B", "L", "R", "auto"],
                                         "description": "文字を置く場所。T=弁当箱の上 B=下 L=左 R=右 auto=おまかせ"},
                               "remove": {"type": "boolean", "description": "2枚目からこのおかずの文字を消す"}},
                               "required": ["index"]}},
            "new_caption": {"type": "string",
                            "description": "キャプションを直す指示があるときだけ、直した後のキャプション全文。"
                                           "指示された所以外は一字一句変えない。ハッシュタグは5個まで"},
            "schedule_at": {"type": "string", "description": "schedule のとき。JST の日時 'YYYY-MM-DD HH:MM'。過去にしない"},
            "redo_memo": {"type": "string", "description": "redo のとき、作り直しに反映するメモ（おかず名の訂正、エピソードなど）"},
            "pen": {"type": "string", "enum": ["", "marker", "thin"], "description": "文字の太さの指示があれば（marker=太め, thin=細め）"},
            "title_style": {"type": "string", "enum": ["", "kakko", "plain", "date"],
                            "description": "タイトルの書き方の指示があれば（kakko=＼〇〇弁当／, plain=〇〇弁当, date=日付つき）。日付を消して→plain"},
            "reply": {"type": "string", "description": "コメントした人への短い返事（日本語、1〜2文、です・ます調）。"
                                                       "none のときは、どう書けば動くかを一言添えて聞き返す"},
        },
        "required": ["action", "reply"],
    },
}

PROMPT = """お弁当の Instagram 投稿の「下書き」に、家族がコメントしました。コメントの意図を読み取って、やることを決めてください。

# いまの日時（JST）
{now}

# 下書きの状態
お弁当名: {title}
状態: {status}{scheduled}
2枚目のタイトルの書き方: {title_style}（kakko=＼〇〇弁当／, plain=〇〇弁当, date=日付つき）

# 2枚目（手書き画像）のおかず（番号: 名前［補足］）
{dishes}

# いまのキャプション
{caption}

# コメント
{comment}

# 判断のしかた
- 「OK」「いいね、出して」「投稿して」など、承認や投稿の意思 → post_now
- 時刻や「今夜」「明日の朝」など、投稿のタイミングの指定 → schedule（「今夜」は20:00、「明日の朝」は7:30、「お昼」は12:00 を目安）
- キャプションの言い回し・ハッシュタグ・一文の追加削除 → new_caption を作る。投稿や予約の指示も一緒なら action はそちら、無ければ caption_only
- 「2枚目」「画像」「文字」についての指示（おかず名の訂正、お弁当名の変更、かっこ書きを消す、文字の場所を上にする、日付を消す、太さ）→ edit_image。
  dish_edits は番号で指定し、頼まれていないおかずは入れない。「全部の（）を消して」なら補足のある全部のおかずに note=""
- お弁当名やおかず名の訂正は、画像（edit_image）とキャプション（new_caption）の両方に反映する
- 写真の読み取り自体が大きく間違っていて、おかずの数や位置から直す必要があるときだけ → redo
- 「やめる」「出さない」「ボツ」→ skip
- 感想や質問など、操作の指示ではない／どちらとも取れる → none（推測で投稿しない）"""


def interpret(text: str, rec: dict, current_caption: str, cfg: dict) -> dict:
    from llm import call_tool
    a = rec.get("analysis") or {}
    return call_tool(cfg["claude"]["model"], TOOL, PROMPT.format(
        now=now_jst().strftime("%Y-%m-%d %H:%M（%a）"),
        title=rec.get("title", ""),
        status={"draft": "確認待ち", "scheduled": "予約済み"}.get(rec.get("status"), rec.get("status")),
        scheduled=f"（{dt.datetime.fromisoformat(rec['scheduled_at']):%m/%d %H:%M}）" if rec.get("scheduled_at") else "",
        title_style=(rec.get("variant") or {}).get("title_style", ""),
        dishes="\n".join(f"{i}: {d.get('name', '')}" + (f"［{d['note']}］" if d.get("note") else "")
                         + ("（非表示）" if d.get("hidden") else "") for i, d in enumerate(a.get("dishes", []))),
        caption=current_caption,
        comment=text,
    ), max_tokens=2500)


def slash(text: str) -> dict | None:
    """従来の /post /redo /skip を同じ形に変換する（Claude を呼ばない近道）。"""
    m = re.match(r"^/(post|redo|skip)\b\s*(.*)$", text, re.S)
    if not m:
        return None
    cmd, arg = m.group(1), m.group(2).strip()
    if cmd == "skip":
        return {"action": "skip"}
    if cmd == "redo":
        return {"action": "redo", "redo_memo": arg}
    if not arg:
        return {"action": "post_now"}
    t = parse_time(arg)
    if not t:
        return {"action": "none", "reply": f"時刻が読み取れませんでした: `{arg}`（例: 19:30、明日 8:00）"}
    return {"action": "schedule", "schedule_at": t.strftime("%Y-%m-%d %H:%M")}


# ------------------------------------------------------------------ 実行
ALLOWED = {"OWNER", "MEMBER", "COLLABORATOR"}
RETRY_MARK = "<!-- retry -->"   # この印のついた返事は「対応済み」に数えない（あとで自動でやり直す）


def _is_retry(body: str) -> bool:
    return RETRY_MARK in body or body.lstrip().startswith("⚠️")


def error_comment(number: int, e: Exception) -> None:
    msg = str(e)
    try:   # 同じエラーの案内を何度も書かない
        last = gh.list_comments(number)[-1]
        if last["user"]["login"] == "github-actions[bot]" and _is_retry(last["body"]):
            return
    except Exception:  # noqa: BLE001
        pass
    if "credit balance is too low" in msg:
        head = "⚠️ Claude API のクレジットが足りず、コメントを読み取れませんでした。チャージされたら自動で対応し直します（コメントし直さなくて大丈夫です）。"
    elif "limit" in msg.lower() and "Claude Code" in msg:
        head = "⚠️ Claude の利用上限に達したので、少し時間をおいて自動でやり直します（コメントし直さなくて大丈夫です）。"
    else:
        head = "⚠️ うまくいきませんでした。次の定期実行で自動でやり直します（コメントし直さなくて大丈夫です）。"
    gh.comment(number, f"{head}\n```\n{msg[:300]}\n```\n{RETRY_MARK}")


def pending_text(number: int) -> str:
    """最後のボットの返事より後の、家族のコメントをまとめて返す。
    続けてコメントすると GitHub が途中の実行を取り消すことがあるので、取りこぼさないよう全部読む。"""
    comments = gh.list_comments(number)
    last_bot = max((i for i, c in enumerate(comments)
                    if c["user"]["login"] == "github-actions[bot]" and not _is_retry(c["body"])), default=-1)
    texts = [c["body"].strip() for c in comments[last_bot + 1:]
             if c.get("author_association") in ALLOWED and c["user"]["type"] != "Bot" and c["body"].strip()]
    return unicodedata.normalize("NFKC", "\n\n".join(texts)).strip()


def main():
    handle(int(env("ISSUE_NUMBER", required=True)), unicodedata.normalize("NFKC", env("COMMENT_BODY", "")).strip())


def sweep() -> None:
    """返事をしていないコメントが残っている下書きを、まとめて見直して対応する（定期実行から呼ばれる）。"""
    for rec in load_json(POSTS_FILE, []):
        if rec.get("status") in ("draft", "scheduled") and rec.get("issue_number"):
            try:
                if pending_text(rec["issue_number"]):
                    print(f"#{rec['issue_number']} に未対応のコメントがあるので対応します")
                    handle(rec["issue_number"])
            except Exception:  # noqa: BLE001  1件の失敗で他を止めない
                traceback.print_exc()


def handle(number: int, fallback_text: str = "") -> None:
    try:
        text = pending_text(number)
    except Exception:  # noqa: BLE001  一覧が取れないときはこのコメントだけ処理
        traceback.print_exc()
        text = fallback_text
    if not text:
        print("未処理のコメントはありません（前の実行で対応済み）")
        return
    print(f"処理するコメント:\n{text}")

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
        issue = gh.get_issue(number)
        body = issue.get("body", "")
        current = post.parse_caption(body) or rec.get("caption", "")
        decision = slash(text) or interpret(text, rec, current, cfg)
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        error_comment(number, e)
        raise
    action = decision.get("action", "none")
    reply = (decision.get("reply") or "").strip()
    print(f"判断: {decision}")

    try:
        # キャプションの修正（投稿・予約と同時でもよい）
        new_cap = (decision.get("new_caption") or "").strip()
        if new_cap and new_cap != current.strip() and action != "redo":
            body = post.replace_caption(body, new_cap)
            gh.update_issue(number, body=body)

        if action == "skip":
            rec.update({"status": "skipped", "scheduled_at": None})
            gh.comment(number, f"🙅 {reply or 'この写真は投稿しないことにしました。'}")
            gh.set_labels(number, ["見送り"])
            gh.update_issue(number, state="closed")

        elif action == "post_now":
            post.publish(rec, cfg)     # 投稿完了のコメントとリンクは publish が書く

        elif action == "schedule":
            try:
                t = dt.datetime.strptime(decision.get("schedule_at", ""), "%Y-%m-%d %H:%M").replace(tzinfo=JST)
            except ValueError:
                t = None
            if not t or t <= now_jst():
                gh.comment(number, "⚠️ 予約の時刻が読み取れませんでした。「今夜8時に」「明日 7:30」のように書いてください。")
                return
            rec.update({"status": "scheduled", "scheduled_at": t.isoformat()})
            msg = f"⏰ **{t:%m/%d %H:%M}** に投稿を予約しました。"
            if new_cap:
                msg += "キャプションも直しておきました。"
            gh.comment(number, msg + "\nそれまでに直したいことがあれば、またコメントしてください。")
            gh.set_labels(number, [post.SCHEDULED_LABEL])

        elif action == "redo":
            import drive
            folder = env("DRIVE_FOLDER_ID") or cfg["drive"]["folder_id"]
            override = {k: decision.get(k) for k in ("pen", "title_style") if decision.get(k)}
            post.build_draft(drive.service(), folder, rec, cfg, strat.load(),
                             extra_memo=decision.get("redo_memo", ""), variant_override=override)
            rec.update({"status": "draft", "scheduled_at": None})
            gh.update_issue(number, title=f"🍱 下書き：{rec['title']}", body=post.issue_body(rec, strat.load()))
            gh.set_labels(number, [post.DRAFT_LABEL])
            gh.comment(number, f"🔁 {reply or '作り直しました。'}\n上の画像とキャプションを確認してください。")

        elif action == "edit_image":
            import drive
            override = {k: decision.get(k) for k in ("pen", "title_style") if decision.get(k)}
            post.rerender_draft(drive.service(), rec, cfg, title=decision.get("image_title"),
                                dish_edits=decision.get("dish_edits"), variant_override=override)
            body = gh.get_issue(number).get("body", "")
            cap_now = post.parse_caption(body) or rec.get("caption", "")
            gh.update_issue(number, title=f"🍱 下書き：{rec['title']}",
                            body=post.replace_caption(post.issue_body(rec, strat.load()), cap_now))
            gh.comment(number, f"🖍 {reply or '2枚目を直しました。'}\n上の画像を確認してください。")

        elif action == "caption_only":
            gh.comment(number, f"✏️ {reply or 'キャプションを直しました。'}\n上の枠が新しいキャプションです。よければ「出して」や時刻をコメントしてください。")

        else:
            gh.comment(number, f"💬 {reply or 'どうしましょう？「出して」「今夜8時に」「作り直して」「やめる」のように書いてもらえれば動きます。'}")
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        rec["error"] = str(e)[:500]
        error_comment(number, e)
        raise
    finally:
        save_json(POSTS_FILE, posts)


if __name__ == "__main__":
    main()
