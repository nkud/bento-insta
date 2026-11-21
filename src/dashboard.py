"""運用状況と分析をまとめたダッシュボード（docs/index.html）を作る。

投稿・承認・集計の各ワークフローの最後に実行され、GitHub Pages で見られる。
  python src/dashboard.py
"""
from __future__ import annotations

import datetime as dt
import html
import pathlib
import statistics as st
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from analyze import ARM_JA, DIM_JA, MAIN_JA, TONE_JA, group_stats, hashtag_stats, post_view  # noqa: E402
from common import env, ACCOUNT_FILE, POSTS_FILE, ROOT, STRATEGY_FILE, load_config, load_json, now_jst  # noqa: E402

E = html.escape
REPO = "nkud/bento-insta"
ACCOUNT = "zaidan_bento"
STATUS_JA = {"draft": "確認待ち", "scheduled": "予約済み", "posted": "投稿済み", "skipped": "見送り", "failed": "失敗"}


def _label(arm: str) -> str:
    return ARM_JA.get(arm) or MAIN_JA.get(arm) or TONE_JA.get(arm) or arm


def _ago(iso: str | None) -> str:
    if not iso:
        return ""
    d = now_jst() - dt.datetime.fromisoformat(iso)
    if d.days >= 1:
        return f"{d.days}日前"
    h = d.seconds // 3600
    return f"{h}時間前" if h else f"{d.seconds // 60}分前"


def _issue(n) -> str:
    return f"https://github.com/{REPO}/issues/{n}"


# ------------------------------------------------------------------ パーツ
def _kpi(label, value, sub=""):
    return (f'<div class="kpi"><div class="kl">{label}</div><div class="kv">{value}</div>'
            f'<div class="ks">{sub}</div></div>')


def _bars(dim: str, arms: dict, best: str | None) -> str:
    rows = sorted(arms.items(), key=lambda kv: -kv[1]["mean"])
    mx = max((a["mean"] for _, a in rows), default=1) or 1
    out = [f'<div class="card"><h3>{DIM_JA.get(dim, dim)}</h3>']
    for arm, a in rows:
        tip = f"{_label(arm)}：スコア {a['mean']}・平均いいね {a['likes_mean']}・{a['n']}件"
        star = ' <span class="best">採用中</span>' if arm == best else ""
        out.append(f'<div class="row" title="{E(tip)}"><span class="lab">{E(_label(arm))}{star}</span>'
                   f'<span class="track"><span class="bar" style="width:{a["mean"] / mx * 100:.1f}%"></span></span>'
                   f'<span class="val">{a["mean"]}</span><span class="n">{a["n"]}件</span></div>')
    out.append("</div>")
    return "".join(out)


def _likes_chart(views) -> str:
    vs = sorted(views, key=lambda v: v["posted_at"])[-30:]
    if not vs:
        return '<p class="empty">投稿から72時間たつと、ここにグラフが出ます</p>'
    W, H, L, B, T = 760, 200, 36, 26, 14
    mx = max(v["likes"] for v in vs) or 1
    bw = (W - L - 10) / len(vs)
    g = [f'<line x1="{L}" y1="{H - B}" x2="{W - 10}" y2="{H - B}" class="axis"/>']
    for frac in (0.5, 1):
        y = H - B - (H - B - T) * frac
        g.append(f'<line x1="{L}" y1="{y:.0f}" x2="{W - 10}" y2="{y:.0f}" class="grid"/>'
                 f'<text x="{L - 6}" y="{y + 4:.0f}" class="ax" text-anchor="end">{int(mx * frac)}</text>')
    for i, v in enumerate(vs):
        h = v["likes"] / mx * (H - B - T)
        x = L + i * bw
        g.append(f'<rect x="{x + 1:.1f}" y="{H - B - h:.1f}" width="{max(bw - 2, 2):.1f}" height="{h:.1f}" rx="3">'
                 f'<title>{v["posted_at"]:%m/%d} {E(v["title"])}\nいいね {int(v["likes"])}・保存 {int(v["saved"])}・リーチ {int(v["reach"])}</title></rect>')
        if i in (0, len(vs) - 1):
            g.append(f'<text x="{x + bw / 2:.0f}" y="{H - 8}" class="ax" text-anchor="middle">{v["posted_at"]:%m/%d}</text>')
    return f'<svg viewBox="0 0 {W} {H}" class="chart" role="img" aria-label="投稿ごとのいいね数">{"".join(g)}</svg>'


def _followers_chart(hist) -> str:
    pts = [(dt.datetime.fromisoformat(h["at"]), h["followers"]) for h in hist if h.get("followers") is not None]
    by_day = {}
    for t, f in pts:
        by_day[t.date()] = f
    days = sorted(by_day.items())[-60:]
    if len(days) < 2:
        return '<p class="empty">毎日の集計が2日分たまると、推移が出ます</p>'
    W, H, L, B, T = 760, 160, 44, 24, 12
    lo, hi = min(f for _, f in days), max(f for _, f in days)
    lo, hi = (lo - 1, hi + 1) if lo == hi else (lo, hi)
    xs = lambda i: L + i / (len(days) - 1) * (W - L - 12)
    ys = lambda f: H - B - (f - lo) / (hi - lo) * (H - B - T)
    path = " ".join(f"{'M' if i == 0 else 'L'}{xs(i):.1f},{ys(f):.1f}" for i, (_, f) in enumerate(days))
    dots = "".join(f'<circle cx="{xs(i):.1f}" cy="{ys(f):.1f}" r="9" class="hit"><title>{d:%m/%d} フォロワー {f}</title></circle>'
                   for i, (d, f) in enumerate(days))
    return (f'<svg viewBox="0 0 {W} {H}" class="chart" role="img" aria-label="フォロワー数の推移">'
            f'<line x1="{L}" y1="{H - B}" x2="{W - 12}" y2="{H - B}" class="axis"/>'
            f'<text x="{L - 6}" y="{ys(hi) + 4:.0f}" class="ax" text-anchor="end">{hi}</text>'
            f'<text x="{L - 6}" y="{ys(lo) + 4:.0f}" class="ax" text-anchor="end">{lo}</text>'
            f'<path d="{path}" class="line"/>{dots}'
            f'<text x="{L}" y="{H - 6}" class="ax">{days[0][0]:%m/%d}</text>'
            f'<text x="{W - 12}" y="{H - 6}" class="ax" text-anchor="end">{days[-1][0]:%m/%d}</text></svg>')


def _queue_card(rec) -> str:
    st_ = rec.get("status")
    img = (rec.get("image_urls") or [None, None])
    thumb = f'<img src="{E(img[1] or img[0])}" alt="" loading="lazy">' if img[0] else '<div class="noimg">🍱</div>'
    when = ""
    if st_ == "scheduled" and rec.get("scheduled_at"):
        when = f'⏰ {dt.datetime.fromisoformat(rec["scheduled_at"]):%m/%d %H:%M} に投稿'
    elif st_ == "draft":
        when = "コメントで /post を待っています"
    elif st_ == "failed":
        when = f'⚠️ {E((rec.get("error") or "")[:90])}'
    link = _issue(rec["issue_number"]) if rec.get("issue_number") else "#"
    return (f'<a class="q" href="{link}" target="_blank" rel="noopener">{thumb}'
            f'<div><span class="pill {st_}">{STATUS_JA.get(st_, st_)}</span>'
            f'<div class="qt">{E(rec.get("title") or rec.get("drive_name", ""))}</div>'
            f'<div class="qs">{when}</div></div></a>')


def _recent_card(rec) -> str:
    m = rec.get("metrics") or {}
    img = (rec.get("image_urls") or [None])[0]
    thumb = f'<img src="{E(img)}" alt="" loading="lazy">' if img else '<div class="noimg">🍱</div>'
    stats = (f'♡ {m.get("likes", "–")}　保存 {m.get("saved", "–")}　リーチ {m.get("reach", "–")}'
             if m else "集計待ち（毎日4時）")
    return (f'<a class="p" href="{E(rec.get("permalink") or "#")}" target="_blank" rel="noopener">{thumb}'
            f'<div class="pt">{E(rec.get("title", ""))}</div>'
            f'<div class="ps">{dt.datetime.fromisoformat(rec["posted_at"]):%m/%d %H:%M}・{_ago(rec["posted_at"])}</div>'
            f'<div class="pm">{stats}</div></a>')


# ------------------------------------------------------------------ 本体
def build() -> str:
    cfg = load_config()
    posts = load_json(POSTS_FILE, [])
    strategy = load_json(STRATEGY_FILE, {})
    hist = load_json(ACCOUNT_FILE, [])
    now = now_jst()

    posted = sorted([p for p in posts if p.get("status") == "posted"], key=lambda p: p["posted_at"], reverse=True)
    queue = [p for p in posts if p.get("status") in ("draft", "scheduled", "failed")]
    queue.sort(key=lambda p: (p.get("status") != "scheduled", p.get("scheduled_at") or "", p.get("drive_created", "")))
    mature_h = cfg["analysis"]["mature_hours"]
    views = [post_view(p) for p in posted if p.get("metrics")
             and (now - dt.datetime.fromisoformat(p["posted_at"])).total_seconds() / 3600 >= mature_h]
    groups = group_stats(views)
    tags = hashtag_stats(views)
    ins = strategy.get("insights") or {}
    need = cfg["analysis"]["min_posts_for_learning"]
    active = len(views) >= need

    followers = hist[-1]["followers"] if hist and hist[-1].get("followers") is not None else None
    f_week = ""
    if hist and followers is not None:
        old = [h for h in hist if dt.datetime.fromisoformat(h["at"]) <= now - dt.timedelta(days=7)]
        if old and old[-1].get("followers") is not None:
            diff = followers - old[-1]["followers"]
            f_week = f"7日で {'+' if diff >= 0 else ''}{diff}"
    week_posts = sum(1 for p in posted if dt.datetime.fromisoformat(p["posted_at"]) >= now - dt.timedelta(days=7))
    avg = lambda k: f"{st.mean(v[k] for v in views):.0f}" if views else "–"

    folder = env("DRIVE_FOLDER_ID") or cfg["drive"].get("folder_id")
    drive_link = f'<a href="https://drive.google.com/drive/folders/{E(folder)}" target="_blank">Drive</a>' if folder else ""

    # パイプライン
    n_draft = sum(1 for p in queue if p["status"] == "draft")
    n_sched = sum(1 for p in queue if p["status"] == "scheduled")
    n_fail = sum(1 for p in queue if p["status"] == "failed")
    steps = [("📷", "Drive に写真", "15分ごとにチェック"),
             ("📝", "下書き", f"{n_draft}件 確認待ち"),
             ("⏰", "予約", f"{n_sched}件"),
             ("📮", "投稿", f"累計 {len(posted)}件"),
             ("📊", "集計", "毎日 4:13"),
             ("🧠", "学習", f"{min(len(views), need)}/{need}件" if not active else "反映中")]
    pipe = "".join(f'<div class="step"><div class="si">{i}</div><div class="sn">{n}</div><div class="sd">{d}</div></div>'
                   + ('<div class="arrow">→</div>' if k < len(steps) - 1 else "")
                   for k, (i, n, d) in enumerate(steps))

    # 学習中の方針
    arms = strategy.get("arms", {})
    policy_rows = []
    for dim, opts in cfg["experiment"]["dimensions"].items():
        stats = arms.get(dim, {})
        best = max(opts, key=lambda o: stats.get(str(o), {}).get("mean", -1)) if stats else None
        tried = "・".join(f'{_label(str(o))}({stats.get(str(o), {}).get("n", 0)})' for o in opts)
        policy_rows.append(f'<tr><td>{DIM_JA.get(dim, dim)}</td><td>{E(_label(str(best))) if best and active else "試行中"}</td>'
                           f'<td class="muted">{E(tried)}</td></tr>')
    eps = int(cfg["experiment"]["epsilon"] * 100)

    li = lambda xs: "".join(f"<li>{E(x)}</li>" for x in xs)
    insight_html = (f'<p>{E(ins["summary"])}</p><h4>分かったこと</h4><ul>{li(ins.get("findings", []))}</ul>'
                    f'<h4>次に作ると良さそうなお弁当・撮り方</h4><ul>{li(ins.get("content_ideas", []))}</ul>'
                    if ins else '<p class="empty">3件以上たまると、毎週月曜に Claude が考察を書きます</p>')
    tips_html = (f'<ul>{li(ins.get("caption_tips", []))}</ul>' if ins.get("caption_tips")
                 else '<p class="empty">学習が始まると、キャプション作成に渡している指示がここに出ます</p>')
    rec_tags = " ".join(E(t) for t in ins.get("recommended_hashtags", [])[:12]) or "–"
    avoid_tags = " ".join(E(t) for t in ins.get("avoid_hashtags", [])[:12]) or "–"
    hours = ins.get("best_posting_hours") or []
    hours_txt = "・".join(f"{h}時" for h in sorted(hours)) if hours else "–"

    dims = list(cfg["experiment"]["dimensions"]) + ["main", "tone", "dish_count", "weekday", "hour"]
    best_of = {d: (max(arms.get(d, {}).items(), key=lambda kv: kv[1]["mean"])[0] if arms.get(d) and active else None)
               for d in dims}
    bars = "".join(_bars(d, groups[d], best_of.get(d)) for d in dims if d in groups) \
        or '<p class="empty">投稿から72時間たった投稿がたまると、パターン別の比較が出ます</p>'
    top = sorted(views, key=lambda v: -v["score"])[:5]
    top_rows = "".join(f'<tr><td><a href="{E(v["permalink"] or "#")}" target="_blank">{E(v["title"])}</a></td>'
                       f'<td>{v["posted_at"]:%m/%d %H:%M}</td><td>{int(v["likes"])}</td><td>{int(v["saved"])}</td>'
                       f'<td>{int(v["reach"])}</td><td><b>{v["score"]}</b></td></tr>' for v in top)
    tag_rows = "".join(f'<tr><td>{E(t["tag"])}</td><td>{t["n"]}</td><td>{t["mean"]}</td>'
                       f'<td class="{"up" if t["lift"] >= 0 else "down"}">{"+" if t["lift"] >= 0 else ""}{t["lift"]}</td></tr>'
                       for t in tags[:12])

    return f"""<!DOCTYPE html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>在宅だんな弁当 ダッシュボード</title>
<style>
:root{{--bg:#f7f3ec;--card:#fffdf9;--ink:#2d2620;--muted:#857869;--accent:#b8662f;--accent2:#e9c9a8;--track:#f0e6da;--line:#e6dccf;--ok:#3f7d4e;--warn:#b07a12;--bad:#b3402f}}
@media (prefers-color-scheme:dark){{:root{{--bg:#1b1815;--card:#25211d;--ink:#f2ece4;--muted:#a99b8c;--accent:#e39a62;--accent2:#5a4330;--track:#352f29;--line:#3a332c;--ok:#7dc08d;--warn:#e0b04a;--bad:#ec7f6c}}}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--ink);font-family:"Hiragino Sans","Noto Sans JP",system-ui,sans-serif;line-height:1.6}}
main{{max-width:1080px;margin:auto;padding:28px 20px 60px}}
header{{display:flex;flex-wrap:wrap;align-items:flex-end;justify-content:space-between;gap:12px;margin-bottom:18px}}
h1{{margin:0;font-size:24px}} h1 small{{font-size:14px;color:var(--muted);font-weight:500;margin-left:8px}}
.upd{{color:var(--muted);font-size:12px}}
nav a{{display:inline-block;margin-left:6px;padding:6px 12px;border-radius:999px;background:var(--card);border:1px solid var(--line);color:var(--ink);text-decoration:none;font-size:13px}}
h2{{font-size:17px;margin:34px 0 12px;display:flex;align-items:center;gap:8px}} h2::before{{content:"";width:4px;height:18px;background:var(--accent);border-radius:2px}}
h3{{font-size:14px;margin:0 0 10px}} h4{{font-size:12px;color:var(--muted);margin:14px 0 4px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px 18px}}
.grid{{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(300px,1fr))}} .wide{{grid-column:1/-1}}
.two{{display:grid;gap:12px;grid-template-columns:minmax(0,3fr) minmax(0,2fr)}} @media (max-width:760px){{.two{{grid-template-columns:1fr}}}}
th{{white-space:nowrap}}
.kpis{{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(150px,1fr))}}
.kpi{{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px 16px}}
.kl{{font-size:12px;color:var(--muted)}} .kv{{font-size:28px;font-weight:700;line-height:1.3}} .ks{{font-size:12px;color:var(--muted)}}
.pipe{{display:flex;align-items:stretch;gap:6px;overflow-x:auto;padding-bottom:4px}}
.step{{flex:1;min-width:110px;background:var(--card);border:1px solid var(--line);border-radius:14px;padding:12px;text-align:center}}
.si{{font-size:22px}} .sn{{font-weight:700;font-size:13px}} .sd{{font-size:12px;color:var(--muted)}} .arrow{{align-self:center;color:var(--muted)}}
.qs-wrap{{display:grid;gap:10px;grid-template-columns:repeat(auto-fill,minmax(300px,1fr))}}
a.q{{display:flex;gap:12px;align-items:center;background:var(--card);border:1px solid var(--line);border-radius:14px;padding:10px;color:var(--ink);text-decoration:none}}
a.q:hover,a.p:hover{{border-color:var(--accent)}}
a.q img,a.q .noimg{{width:72px;height:90px;object-fit:cover;border-radius:8px;background:var(--track);display:grid;place-items:center;flex:none}}
.qt{{font-weight:700;font-size:14px}} .qs{{font-size:12px;color:var(--muted)}}
.pill{{display:inline-block;font-size:11px;padding:1px 8px;border-radius:999px;background:var(--track)}}
.pill.scheduled{{color:var(--ok)}} .pill.draft{{color:var(--warn)}} .pill.failed{{color:var(--bad)}}
.posts{{display:grid;gap:12px;grid-template-columns:repeat(auto-fill,minmax(170px,1fr))}}
a.p{{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:8px;color:var(--ink);text-decoration:none}}
a.p img,a.p .noimg{{width:100%;aspect-ratio:4/5;object-fit:cover;border-radius:10px;background:var(--track);display:grid;place-items:center}}
.pt{{font-weight:700;font-size:13px;margin-top:6px}} .ps,.pm{{font-size:11px;color:var(--muted)}}
.row{{display:grid;grid-template-columns:150px 1fr 42px 36px;align-items:center;gap:8px;font-size:13px;padding:4px 2px;border-radius:6px}}
.row:hover{{background:var(--track)}} .track{{background:var(--track);border-radius:4px;height:10px}}
.bar{{display:block;height:10px;background:var(--accent);border-radius:0 4px 4px 0}} .val{{text-align:right}} .n{{color:var(--muted);font-size:11px}}
.lab{{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}} .best{{font-size:10px;color:var(--ok);border:1px solid currentColor;border-radius:999px;padding:0 5px;margin-left:2px}}
.chart{{width:100%;height:auto}} .chart rect{{fill:var(--accent)}} .chart rect:hover{{opacity:.75}}
.axis{{stroke:var(--line)}} .grid line,.chart .grid{{stroke:var(--line);stroke-dasharray:2 4}} .ax{{fill:var(--muted);font-size:11px}}
.line{{fill:none;stroke:var(--accent);stroke-width:2}} .hit{{fill:transparent}} .hit:hover{{fill:var(--accent);fill-opacity:.25}}
table{{width:100%;border-collapse:collapse;font-size:13px}} th,td{{text-align:left;padding:7px 6px;border-bottom:1px solid var(--line)}} th{{color:var(--muted);font-weight:500;font-size:12px}}
.muted{{color:var(--muted);font-size:12px}} .up{{color:var(--ok)}} .down{{color:var(--bad)}} a{{color:var(--accent)}}
.empty{{color:var(--muted);font-size:13px;margin:6px 0}}
code{{background:var(--track);padding:1px 6px;border-radius:6px;font-size:12px}}
.tagline{{font-size:13px;line-height:1.9}}
.progress{{height:8px;background:var(--track);border-radius:4px;overflow:hidden;margin:6px 0 2px}} .progress span{{display:block;height:100%;background:var(--accent)}}
</style></head><body><main>

<header>
  <div><h1>🍱 在宅だんな弁当<small>@{ACCOUNT}</small></h1>
  <div class="upd">更新 {now:%Y/%m/%d %H:%M}（投稿・承認・集計のたびに自動更新）</div></div>
  <nav><a href="https://www.instagram.com/{ACCOUNT}/" target="_blank">Instagram</a><a href="https://github.com/{REPO}/issues?q=is%3Aopen+label%3A下書き" target="_blank">下書き一覧</a><a href="https://github.com/{REPO}/actions" target="_blank">Actions</a>{drive_link}</nav>
</header>

<div class="kpis">
{_kpi("フォロワー", followers if followers is not None else "–", f_week)}
{_kpi("投稿数", len(posted), f"この7日で {week_posts}件")}
{_kpi("平均いいね", avg("likes"), "72時間後の値")}
{_kpi("平均保存", avg("saved"), "")}
{_kpi("平均リーチ", avg("reach"), "")}
{_kpi("確認待ち", n_draft + n_sched, f"予約 {n_sched}件" + (f"・失敗 {n_fail}件" if n_fail else ""))}
</div>

<h2>運用の流れ</h2>
<div class="pipe">{pipe}</div>

<h2>確認待ち・予約中</h2>
<div class="qs-wrap">{"".join(_queue_card(p) for p in queue) or '<p class="empty">確認待ちの下書きはありません。Drive に写真を入れると15分以内にここに出ます</p>'}</div>

<h2>最近の投稿</h2>
<div class="posts">{"".join(_recent_card(p) for p in posted[:10]) or '<p class="empty">まだ投稿はありません</p>'}</div>

<h2>反応の推移</h2>
<div class="grid">
<div class="card wide"><h3>投稿ごとのいいね数</h3>{_likes_chart(views)}</div>
<div class="card wide"><h3>フォロワー数</h3>{_followers_chart(hist)}</div>
</div>

<h2>学習の状況</h2>
<div class="two">
<div class="card"><h3>{"分析結果を投稿に反映中" if active else "データ集め中"}</h3>
<div class="progress"><span style="width:{min(len(views) / need, 1) * 100:.0f}%"></span></div>
<div class="muted">評価できた投稿 {len(views)} / {need}件（{need}件で反映開始）</div>
<table style="margin-top:10px"><tr><th>試していること</th><th>いま採用中</th><th>試した回数</th></tr>{"".join(policy_rows)}</table>
<div class="muted" style="margin-top:8px">{100 - eps}% は一番良いパターン、{eps}% は他のパターンも試し続けます</div></div>
<div class="card"><h3>キャプションに反映している指示</h3>{tips_html}
<h4>伸びやすいタグ</h4><div class="tagline">{rec_tags}</div>
<h4>効果が薄いタグ</h4><div class="tagline">{avoid_tags}</div>
<h4>伸びやすい時間帯</h4><div>{hours_txt}</div></div>
</div>
<div class="card" style="margin-top:12px"><h3>Claude の考察（毎週月曜）</h3>{insight_html}</div>

<h2>パターン別の比較</h2>
<p class="muted">スコア =（いいね ＋ 保存×2 ＋ シェア×2 ＋ コメント×3）÷ リーチ × 100。件数が少ない項目の差は偶然のことが多いので参考程度に。</p>
<div class="grid">{bars}</div>

<div class="grid" style="margin-top:12px">
<div class="card"><h3>スコア上位の投稿</h3>{f'<table><tr><th>お弁当</th><th>投稿</th><th>♡</th><th>保存</th><th>リーチ</th><th>スコア</th></tr>{top_rows}</table>' if top_rows else '<p class="empty">まだありません</p>'}</div>
<div class="card"><h3>ハッシュタグ別（3回以上・平均との差）</h3>{f'<table><tr><th>タグ</th><th>回数</th><th>スコア</th><th>差</th></tr>{tag_rows}</table>' if tag_rows else '<p class="empty">まだありません</p>'}</div>
</div>

<h2>使い方メモ</h2>
<div class="grid">
<div class="card"><h3>下書き Issue へのコメント</h3><table>
<tr><td><code>/post</code></td><td>今すぐ投稿</td></tr>
<tr><td><code>/post 19:30</code></td><td>時刻を指定して予約（<code>/post 明日 8:00</code> も可）</td></tr>
<tr><td><code>/redo メモ</code></td><td>作り直し（メモはキャプションに反映）</td></tr>
<tr><td><code>/skip</code></td><td>投稿しない</td></tr></table>
<div class="muted" style="margin-top:8px">キャプションは Issue 本文の枠内を編集すると、そのまま投稿されます</div></div>
<div class="card"><h3>自動で動いているもの</h3><table>
<tr><td>15分ごと</td><td>Drive の新しい写真 → 下書き作成／予約時刻の投稿</td></tr>
<tr><td>毎日 4:13</td><td>いいね・保存・リーチ・フォロワーを集計</td></tr>
<tr><td>毎週月曜</td><td>Claude が考察を更新し、キャプションの方針に反映</td></tr></table>
<div class="muted" style="margin-top:8px">写真の「説明」欄や同名の .txt に書いたメモはキャプションに入ります</div></div>
</div>

</main></body></html>"""


def main():
    out = ROOT / "docs" / "index.html"
    out.parent.mkdir(exist_ok=True)
    out.write_text(build(), encoding="utf-8")
    print(f"ダッシュボードを更新しました: {out}")


if __name__ == "__main__":
    main()
