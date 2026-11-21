"""集めた指標を分析して、①レポート（HTML/Markdown）②次回以降の方針 strategy.json を作る。

スコア = (いいね + 保存×2 + シェア×2 + コメント×3) ÷ リーチ × 100
  → フォロワーが増えても公平に比べられるよう、リーチあたりで評価します。
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import pathlib
import statistics as st
import sys
from collections import defaultdict

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from common import DATA, POSTS_FILE, ROOT, STRATEGY_FILE, load_config, load_json, now_jst, save_json  # noqa: E402

WEEK = "月火水木金土日"
MAIN_JA = {"meat": "肉", "fish": "魚", "egg": "卵", "tofu_veg": "豆腐・野菜", "rice_dish": "ご飯もの",
           "noodle": "麺", "other": "その他"}
TONE_JA = {"brown": "茶色", "colorful": "カラフル", "green": "緑多め", "white": "白っぽい", "other": "その他"}
DIM_JA = {"title_style": "タイトルの書き方", "font": "手書きフォント", "caption_length": "コメントの長さ",
          "hashtag_count": "ハッシュタグ数", "main": "主菜", "tone": "色味", "dish_count": "品数",
          "weekday": "曜日", "hour": "投稿時間帯"}
ARM_JA = {"kakko": "＼〇〇弁当／", "plain": "〇〇弁当", "date": "日付＋〇〇弁当",
          "yomogi": "よもぎ", "klee": "クレー", "kurenaido": "くれないど", "short": "短め", "long": "長め"}


def _num(v):
    return float(v) if isinstance(v, (int, float)) else 0.0


def _band(h: int) -> str:
    """件数が少ないうちは1時間単位だとバラつくので、時間帯でまとめる。"""
    for lo, hi, name in ((5, 9, "朝 5-9時"), (10, 13, "昼 10-13時"), (14, 17, "夕方 14-17時"), (18, 21, "夜 18-21時")):
        if lo <= h <= hi:
            return name
    return "深夜 22-4時"


def post_view(p: dict) -> dict:
    m = p.get("snapshots", {}).get("72") or p["metrics"]
    reach = _num(m.get("reach"))
    likes, saved, shares, com = (_num(m.get(k)) for k in ("likes", "saved", "shares", "comments"))
    score = (likes + 2 * saved + 2 * shares + 3 * com) / reach * 100 if reach else 0.0
    t = dt.datetime.fromisoformat(p["posted_at"])
    a = p.get("analysis", {})
    n = len(a.get("dishes", []))
    return {
        "id": p["ig_media_id"], "title": p.get("title", ""), "permalink": p.get("permalink"),
        "posted_at": t, "likes": likes, "saved": saved, "comments": com, "shares": shares,
        "reach": reach, "score": round(score, 2), "caption": p.get("caption", ""),
        "hashtags": p.get("hashtags", []),
        "groups": {**{k: str(v) for k, v in p.get("variant", {}).items()},
                   "main": a.get("main", "other"), "tone": a.get("tone", "other"),
                   "dish_count": "3品以下" if n <= 3 else ("4〜5品" if n <= 5 else "6品以上"),
                   "weekday": WEEK[t.weekday()], "hour": _band(t.hour)},
    }


def group_stats(views: list[dict]) -> dict:
    g = defaultdict(lambda: defaultdict(list))
    for v in views:
        for dim, arm in v["groups"].items():
            g[dim][arm].append(v)
    out = {}
    for dim, arms in g.items():
        out[dim] = {arm: {"n": len(vs), "mean": round(st.mean(x["score"] for x in vs), 2),
                          "likes_mean": round(st.mean(x["likes"] for x in vs), 1)}
                    for arm, vs in arms.items()}
    return out


def hashtag_stats(views, min_uses=3):
    g = defaultdict(list)
    for v in views:
        for t in v["hashtags"]:
            g[t].append(v["score"])
    base = st.mean(v["score"] for v in views) if views else 0
    rows = [{"tag": t, "n": len(s), "mean": round(st.mean(s), 2), "lift": round(st.mean(s) - base, 2)}
            for t, s in g.items() if len(s) >= min_uses]
    return sorted(rows, key=lambda r: -r["lift"])


# ------------------------------------------------------------------ Claude による考察
TOOL = {
    "name": "report_insights",
    "description": "投稿データから分かったことと次回への方針",
    "input_schema": {
        "type": "object",
        "properties": {
            "summary": {"type": "string", "description": "全体の傾向を3〜4文で"},
            "findings": {"type": "array", "items": {"type": "string"}, "description": "根拠つきの発見 3〜6個"},
            "caption_tips": {"type": "array", "items": {"type": "string"},
                             "description": "次のキャプション生成に渡す具体的な指示 2〜5個"},
            "recommended_hashtags": {"type": "array", "items": {"type": "string"}},
            "avoid_hashtags": {"type": "array", "items": {"type": "string"}},
            "content_ideas": {"type": "array", "items": {"type": "string"},
                              "description": "次に作ると伸びそうなお弁当・撮り方のアイデア 2〜4個"},
            "best_posting_hours": {"type": "array", "items": {"type": "integer"},
                                   "description": "データ上よさそうな投稿時刻（JST, 3〜6個）。根拠が弱ければ空"},
        },
        "required": ["summary", "findings", "caption_tips", "recommended_hashtags", "avoid_hashtags",
                     "content_ideas", "best_posting_hours"],
    },
}


def llm_insights(views, groups, tags, model) -> dict:
    from llm import call_tool
    top = sorted(views, key=lambda v: -v["score"])[:3]
    bottom = sorted(views, key=lambda v: v["score"])[:3]
    brief = lambda v: {"title": v["title"], "score": v["score"], "likes": v["likes"], "saved": v["saved"],
                       "reach": v["reach"], "posted": v["posted_at"].strftime("%a %H:%M"),
                       "caption": v["caption"][:400]}
    payload = {"posts": len(views), "groups": groups, "hashtags_top": tags[:15], "hashtags_bottom": tags[-10:],
               "top_posts": [brief(v) for v in top], "bottom_posts": [brief(v) for v in bottom]}
    prompt = ("お弁当の Instagram アカウントの投稿データです。スコアは (いいね+保存×2+シェア×2+コメント×3)/リーチ×100。"
              "件数(n)が少ないグループの差は偶然の可能性が高いので、断定せず慎重に扱ってください。"
              "数字の根拠を示しながら、次の投稿で何を変えるとよいか具体的にまとめてください。\n\n"
              + json.dumps(payload, ensure_ascii=False, default=str))
    return call_tool(model, TOOL, prompt, max_tokens=3000)


# ------------------------------------------------------------------ レポート
def _bars(dim: str, arms: dict) -> str:
    rows = sorted(arms.items(), key=lambda kv: -kv[1]["mean"])
    mx = max((a["mean"] for _, a in rows), default=1) or 1
    out = [f'<div class="card"><h3>{DIM_JA.get(dim, dim)}</h3>']
    for arm, a in rows:
        label = ARM_JA.get(arm) or MAIN_JA.get(arm) or TONE_JA.get(arm) or arm
        w = a["mean"] / mx * 100
        tip = f"{label}: スコア {a['mean']} / 平均いいね {a['likes_mean']} / {a['n']}件"
        out.append(f'<div class="row" title="{html.escape(tip)}"><span class="lab">{html.escape(label)}</span>'
                   f'<span class="track"><span class="bar" style="width:{w:.1f}%"></span></span>'
                   f'<span class="val">{a["mean"]}</span><span class="n">n={a["n"]}</span></div>')
    out.append("</div>")
    return "".join(out)


def _trend(views) -> str:
    vs = sorted(views, key=lambda v: v["posted_at"])
    if not vs:
        return ""
    W, H, pad = 720, 160, 24
    mx = max(v["likes"] for v in vs) or 1
    bw = (W - pad * 2) / len(vs)
    bars = []
    for i, v in enumerate(vs):
        h = v["likes"] / mx * (H - pad * 2)
        x, y = pad + i * bw, H - pad - h
        bars.append(f'<rect x="{x + 1:.1f}" y="{y:.1f}" width="{max(bw - 2, 2):.1f}" height="{h:.1f}" rx="2">'
                    f'<title>{v["posted_at"]:%m/%d} {html.escape(v["title"])}: いいね {int(v["likes"])}</title></rect>')
    return (f'<div class="card wide"><h3>投稿ごとのいいね数（古い→新しい）</h3>'
            f'<svg viewBox="0 0 {W} {H}" class="trend" role="img" aria-label="いいね数の推移">'
            f'<line x1="{pad}" y1="{H - pad}" x2="{W - pad}" y2="{H - pad}" class="axis"/>{"".join(bars)}'
            f'<text x="{pad}" y="14" class="axlab">最大 {int(mx)}</text></svg></div>')


def render_html(views, groups, tags, ins, active, cfg) -> str:
    n = len(views)
    kpi = lambda label, val: f'<div class="kpi"><div class="kv">{val}</div><div class="kl">{label}</div></div>'
    avg = lambda k: round(st.mean(v[k] for v in views), 1) if views else "-"
    top = sorted(views, key=lambda v: -v["score"])[:5]
    li = lambda xs: "".join(f"<li>{html.escape(x)}</li>" for x in xs)
    dims = list(cfg["experiment"]["dimensions"]) + ["main", "tone", "dish_count", "weekday", "hour"]
    status = ("学習中：分析結果を次の投稿に反映しています" if active else
              f"データ収集中：{cfg['analysis']['min_posts_for_learning']}件たまったら反映を始めます")
    top_rows = "".join(
        f'<tr><td><a href="{html.escape(v["permalink"] or "#")}">{html.escape(v["title"])}</a></td>'
        f'<td>{v["posted_at"]:%m/%d %H:%M}</td><td>{int(v["likes"])}</td><td>{int(v["saved"])}</td>'
        f'<td>{int(v["reach"])}</td><td>{v["score"]}</td></tr>' for v in top)
    tag_rows = "".join(f'<tr><td>{html.escape(t["tag"])}</td><td>{t["n"]}</td><td>{t["mean"]}</td>'
                       f'<td>{"+" if t["lift"] >= 0 else ""}{t["lift"]}</td></tr>' for t in tags[:12])
    ins_html = ""
    if ins:
        ins_html = (f'<div class="card wide"><h3>Claude の考察</h3><p>{html.escape(ins["summary"])}</p>'
                    f'<h4>分かったこと</h4><ul>{li(ins["findings"])}</ul>'
                    f'<h4>次のキャプションに反映すること</h4><ul>{li(ins["caption_tips"])}</ul>'
                    f'<h4>次に作ると良さそうなお弁当・撮り方</h4><ul>{li(ins["content_ideas"])}</ul></div>')
    return f"""<!DOCTYPE html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>お弁当インスタ レポート</title>
<style>
:root{{--bg:#faf7f2;--card:#fff;--ink:#2b2622;--muted:#7a6f66;--bar:#c8743a;--track:#efe6dc;--line:#e4dbd1}}
@media (prefers-color-scheme:dark){{:root{{--bg:#1d1a17;--card:#27231f;--ink:#f1ebe4;--muted:#a89b8f;--bar:#e0935a;--track:#3a342e;--line:#3a342e}}}}
body{{margin:0;background:var(--bg);color:var(--ink);font-family:"Hiragino Sans","Noto Sans JP",sans-serif}}
main{{max-width:980px;margin:auto;padding:24px}} h1{{font-size:22px;margin:0 0 4px}} .sub{{color:var(--muted);font-size:13px}}
.kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;margin:18px 0}}
.kpi,.card{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px}}
.kv{{font-size:26px;font-weight:700}} .kl{{color:var(--muted);font-size:12px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:12px}} .wide{{grid-column:1/-1}}
h3{{font-size:15px;margin:0 0 10px}} h4{{font-size:13px;margin:14px 0 4px;color:var(--muted)}}
.row{{display:grid;grid-template-columns:110px 1fr 44px 40px;align-items:center;gap:8px;font-size:13px;padding:3px 0}}
.row:hover{{background:var(--track)}} .track{{background:var(--track);border-radius:4px;height:10px}}
.bar{{display:block;height:10px;background:var(--bar);border-radius:0 4px 4px 0}} .val{{text-align:right}} .n{{color:var(--muted);font-size:11px}}
.trend{{width:100%;height:auto}} .trend rect{{fill:var(--bar)}} .trend rect:hover{{opacity:.7}} .axis{{stroke:var(--line)}} .axlab{{fill:var(--muted);font-size:11px}}
table{{width:100%;border-collapse:collapse;font-size:13px}} td,th{{padding:6px;border-bottom:1px solid var(--line);text-align:left}}
a{{color:var(--bar)}} .status{{display:inline-block;margin-top:8px;padding:4px 10px;border-radius:999px;background:var(--track);font-size:12px}}
</style></head><body><main>
<h1>お弁当インスタ レポート</h1><div class="sub">{now_jst():%Y/%m/%d %H:%M} 更新 ・ 投稿から{cfg['analysis']['mature_hours']}時間以上たった {n} 件で集計</div>
<div class="status">{status}</div>
<div class="kpis">{kpi("集計した投稿", n)}{kpi("平均いいね", avg("likes"))}{kpi("平均保存", avg("saved"))}{kpi("平均リーチ", avg("reach"))}{kpi("平均スコア", avg("score"))}</div>
<div class="grid">{ins_html}{_trend(views)}
{''.join(_bars(d, groups[d]) for d in dims if d in groups)}
<div class="card wide"><h3>スコア上位の投稿</h3><table><tr><th>お弁当</th><th>投稿</th><th>いいね</th><th>保存</th><th>リーチ</th><th>スコア</th></tr>{top_rows}</table></div>
<div class="card wide"><h3>ハッシュタグ別（3回以上使ったもの・平均との差）</h3><table><tr><th>タグ</th><th>回数</th><th>平均スコア</th><th>差</th></tr>{tag_rows}</table></div>
</div><p class="sub">スコア = (いいね + 保存×2 + シェア×2 + コメント×3) ÷ リーチ × 100。n が小さい項目の差は偶然のことが多いので参考程度に。</p>
</main></body></html>"""


def render_md(views, ins, active) -> str:
    lines = [f"## お弁当インスタ レポート（{now_jst():%Y/%m/%d}）", f"- 集計対象: {len(views)} 件",
             f"- 反映状態: {'学習中' if active else 'データ収集中'}"]
    if views:
        lines.append(f"- 平均いいね {st.mean(v['likes'] for v in views):.1f} / 平均スコア {st.mean(v['score'] for v in views):.2f}")
    if ins:
        lines += ["", "### 考察", ins["summary"], "", *[f"- {f}" for f in ins["findings"]],
                  "", "### 次に試すこと", *[f"- {c}" for c in ins["content_ideas"]]]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-llm", action="store_true")
    args = ap.parse_args()
    cfg = load_config()
    posts = load_json(POSTS_FILE, [])
    now = now_jst()
    mature = cfg["analysis"]["mature_hours"]
    views = [post_view(p) for p in posts
             if p.get("status") == "posted" and p.get("metrics")
             and (now - dt.datetime.fromisoformat(p["posted_at"])).total_seconds() / 3600 >= mature]
    groups = group_stats(views)
    tags = hashtag_stats(views)
    active = len(views) >= cfg["analysis"]["min_posts_for_learning"]

    old = load_json(STRATEGY_FILE, {})
    ins = old.get("insights")
    if views and len(views) >= 3 and not args.no_llm and os.environ.get("ANTHROPIC_API_KEY"):
        ins = llm_insights(views, groups, tags, cfg["claude"]["model"])

    strategy = {
        "updated_at": now.isoformat(), "n_posts": len(views), "learning_active": active,
        "arms": {d: groups.get(d, {}) for d in cfg["experiment"]["dimensions"]},
        "groups": groups, "hashtags": tags[:40], "insights": ins,
    }
    save_json(STRATEGY_FILE, strategy)
    (ROOT / "docs").mkdir(exist_ok=True)
    (ROOT / "docs" / "report.html").write_text(render_html(views, groups, tags, ins, active, cfg), encoding="utf-8")
    md = render_md(views, ins, active)
    (DATA / "report.md").write_text(md, encoding="utf-8")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(md + "\n")
    print(md)


if __name__ == "__main__":
    main()
