"""2枚目用：写真におかず名を手書き風に書き込む（スマホで指書きしたような雰囲気）。

- 白いペンでさっと書いたような文字（影はごく薄く）
- ラベルごとに少し傾ける、文字ごとにも揺らす
- 余白が狭いところは縦書き
- 引き出し線は先にくるっと輪を描く、矢じりなしの曲線
- タイトルは ＼ 〇〇弁当 ／ の斜線を手で引いた線で
"""
from __future__ import annotations

import math
import random

from PIL import Image, ImageDraw, ImageFilter, ImageStat

from common import FONTS

import handwriting

# 文字の書き方
#   "font"    : 手書き風フォント（いまは よもぎ）で書く
#   "strokes" : 書き順データ（KanjiVG）を1画ずつペンでなぞって書く
LETTERING = "font"
LETTER_FONT = "Yomogi-Regular.ttf"
FALLBACK_FONT = "YuseiMagic-Regular.ttf"   # strokes のとき、書き順データに無い文字だけこのフォントで
# ペンの太さ。実験軸 pen で切り替える（font: 輪郭を太らせるピクセル数 / strokes: 文字サイズに対する線の太さ）
PEN_WIDTH = {"font": {"marker": 2, "thin": 1}, "strokes": {"marker": 0.095, "thin": 0.068}}
# 縦書きのときに90度回す文字
VERT_ROTATE = set("ーｰ〜～-—…（）()「」×")
WHITE = (255, 255, 255, 255)
DARK = (58, 40, 30, 255)


# ---------------------------------------------------------------- crop
def crop_portrait(img: Image.Image, bbox: dict, W: int, H: int):
    """弁当箱が中央に来て、左右に書き込み用の余白が残るよう 4:5 に切り出す。
    戻り値: (切り出し後画像, 元画像の正規化座標→出力ピクセル座標 の変換関数)"""
    w, h = img.size
    r = W / H
    # 画像に収まる最大の 4:5 枠
    max_cw, max_ch = (h * r, h) if w / h > r else (w, w / r)
    bx0, by0, bx1, by1 = (bbox["x0"] * w, bbox["y0"] * h, bbox["x1"] * w, bbox["y1"] * h)
    bw, bh = max(bx1 - bx0, 1), max(by1 - by0, 1)
    # 弁当箱が幅の 66%・高さの 72% 程度になる大きさ（余白にラベルを置くため）
    cw = max(bw / 0.66, bh / 0.72 * r)
    cw = min(max(cw, max_cw * 0.45), max_cw)
    ch = cw / r
    cx, cy = (bx0 + bx1) / 2, (by0 + by1) / 2
    left = min(max(cx - cw / 2, 0), w - cw)
    top = min(max(cy - ch / 2, 0), h - ch)
    out = img.crop((round(left), round(top), round(left + cw), round(top + ch))).resize((W, H), Image.LANCZOS)
    sx, sy = W / cw, H / ch

    def to_px(nx: float, ny: float):
        return ((nx * w - left) * sx, (ny * h - top) * sy)

    return out, to_px


# ---------------------------------------------------------------- text
def _pen(variant: dict, size: int):
    w = PEN_WIDTH[LETTERING].get(variant.get("pen", "thin"), PEN_WIDTH[LETTERING]["thin"])
    if LETTERING == "strokes":
        return handwriting.Pen(size, FALLBACK_FONT, w)
    return handwriting.FontPen(size, LETTER_FONT, round(w * size / 54))   # 小さい字は太らせすぎない


def _wrap(text: str, n: int) -> list[str]:
    """日本語を折り返す。n+2 文字までは1行、それ以上は行の長さが揃うよう均等に分ける。"""
    if len(text) <= n + 2:
        return [text]
    k = -(-len(text) // n)
    size = -(-len(text) // k)
    return [text[i:i + size] for i in range(0, len(text), size)]


SP = 1.04       # 字間（少しゆったり）
LH = 1.22       # 行の高さ
VC = 1.08       # 縦書きの字送り
VW = 1.25       # 縦書きの列幅


def _measure(lines: list[str], pen, vertical: bool = False) -> tuple[float, float]:
    if vertical:
        return len(lines) * pen.size * VW, max(len(l) for l in lines) * pen.size * VC
    return max(pen.length(l) for l in lines) * SP, len(lines) * pen.size * LH


def _char(layer, ch, pen, center, fill, rng, rot90=False):
    tile = pen.tile(ch, fill, rng, rot90=rot90)
    x, y = center
    layer.alpha_composite(tile, (int(x - tile.width / 2), int(y - tile.height / 2)))


def _draw_lines(layer, lines, pen, center, fill, rng, vertical=False):
    w, h = _measure(lines, pen, vertical)
    cx, cy = center
    s = pen.size
    if not vertical:
        y = cy - h / 2
        for line in lines:
            x = cx - pen.length(line) * SP / 2
            for ch in line:
                adv = pen.advance(ch) * SP
                _char(layer, ch, pen, (x + adv / 2, y + s * LH / 2 + rng.uniform(-2.5, 2.5)), fill, rng)
                x += adv * rng.uniform(0.97, 1.03)
            y += s * LH
    else:                                         # 右の列から左へ
        x = cx + w / 2 - s * VW / 2
        for line in lines:
            y = cy - h / 2
            for ch in line:
                _char(layer, ch, pen, (x + rng.uniform(-2.5, 2.5), y + s * VC / 2), fill, rng,
                      rot90=ch in VERT_ROTATE)
                y += s * VC * rng.uniform(0.97, 1.03)
            x -= s * VW


def _with_shadow(base: Image.Image, layer: Image.Image, glow=(0, 0, 0)) -> Image.Image:
    """ごく薄い影。どんな背景でも読める最低限だけ付けて、ペン書きの軽さを残す。"""
    a = layer.getchannel("A")
    out = base
    for radius, strength in ((7, 0.22), (1.6, 0.38)):
        sh = Image.new("RGBA", base.size, glow + (0,))
        sh.putalpha(a.filter(ImageFilter.GaussianBlur(radius)).point(lambda v: int(v * strength)))
        out = Image.alpha_composite(out, sh)
    return Image.alpha_composite(out, layer)


# ---------------------------------------------------------------- lines
def _jitter_line(draw, pts, fill, width, rng):
    pts = [(x + rng.uniform(-0.5, 0.5), y + rng.uniform(-0.5, 0.5)) for x, y in pts]
    draw.line(pts, fill=fill, width=width, joint="curve")
    r = width / 2                                  # 線の端を丸く
    for x, y in (pts[0], pts[-1]):
        draw.ellipse((x - r, y - r, x + r, y + r), fill=fill)


def _swoosh(draw, p0, p1, bend, fill, rng, width=3):
    """ゆるくカーブした線の途中に、くるっと小さな輪（ℓ のような）を入れる。矢じりなし。"""
    (x0, y0), (x1, y1) = p0, p1
    dx, dy = x1 - x0, y1 - y0
    dist = math.hypot(dx, dy) or 1
    nx, ny = -dy / dist, dx / dist
    mx, my = (x0 + x1) / 2 + nx * bend * dist, (y0 + y1) / 2 + ny * bend * dist
    n = 48
    pts = [((1 - t) ** 2 * x0 + 2 * (1 - t) * t * mx + t ** 2 * x1,
            (1 - t) ** 2 * y0 + 2 * (1 - t) * t * my + t ** 2 * y1) for t in [i / n for i in range(n + 1)]]
    # 輪を入れる位置（ラベル寄り or おかず寄り）
    k = int(n * rng.choice((0.22, 0.25, 0.78)))
    (ax, ay), (bx, by) = pts[k - 1], pts[k + 1]
    tl = math.hypot(bx - ax, by - ay) or 1
    ux, uy = (bx - ax) / tl, (by - ay) / tl
    side = 1 if bend >= 0 else -1
    r = rng.uniform(7, 10)
    px, py = pts[k]
    cx, cy = px - uy * r * side, py + ux * r * side
    a0 = math.atan2(py - cy, px - cx)
    loop = [(cx + r * math.cos(a0 - side * th) * 1.1, cy + r * math.sin(a0 - side * th))
            for th in [j / 16 * 2 * math.pi for j in range(17)]]
    _jitter_line(draw, pts[:k] + loop + pts[k:], fill, width, rng)


def _slashes(draw, box, fill, rng, width=5):
    """タイトル左右の ＼ ／ を手で引いた線で。"""
    x0, y0, x1, y1 = box
    h = (y1 - y0) * 0.75
    cy = (y0 + y1) / 2
    for sign, x in ((-1, x0 - 26), (1, x1 + 26)):
        top = (x + sign * h * 0.42, cy - h / 2)
        bot = (x, cy + h / 2)
        pts = [(top[0] + (bot[0] - top[0]) * t, top[1] + (bot[1] - top[1]) * t + rng.uniform(-1, 1))
               for t in [i / 8 for i in range(9)]]
        _jitter_line(draw, pts, fill, width, rng)


# ---------------------------------------------------------------- layout
def _title(title: str, style: str, date) -> str:
    if style == "date" and date is not None:
        return f"{date.month}.{date.day} {title}"
    return title


def _ink_for(img: Image.Image, box) -> tuple:
    """ラベル位置の明るさを見て、白文字か焦げ茶文字かを選ぶ。"""
    x0, y0, x1, y1 = [int(v) for v in box]
    x0, y0 = max(x0, 0), max(y0, 0)
    x1, y1 = min(x1, img.width), min(y1, img.height)
    if x1 <= x0 or y1 <= y0:
        return WHITE
    lum = ImageStat.Stat(img.crop((x0, y0, x1, y1)).convert("L")).mean[0]
    return DARK if lum > 205 else WHITE


GAP = 26


def _resolve(items, W, H, title_bottom, bento_px):
    """同じ辺に並ぶラベル同士が重ならないよう押し広げる。上下の段を先に決め、左右の列はその間に収める。"""
    bx0, by0, bx1, by1 = bento_px
    top_lim, bot_lim = title_bottom + 10, H - 30
    for side in "TB":
        row = sorted([i for i in items if i["side"] == side], key=lambda i: i["px"])
        for i in row:
            i["cx"] = i["px"]
            if side == "T":   # 弁当箱のすぐ上（タイトルとは重ねない）
                i["cy"] = max(by0 - 46 - i["h"] / 2, title_bottom + 10 + i["h"] / 2)
            else:             # 弁当箱のすぐ下
                i["cy"] = min(by1 + 46 + i["h"] / 2, H - 30 - i["h"] / 2)
        x = 30
        for i in row:
            i["cx"] = max(i["cx"], x + i["w"] / 2)
            x = i["cx"] + i["w"] / 2 + 34
        x = W - 30
        for i in reversed(row):
            i["cx"] = min(i["cx"], x - i["w"] / 2)
            x = i["cx"] - i["w"] / 2 - 34
        if row and side == "T":
            top_lim = max(top_lim, max(i["cy"] + i["h"] / 2 for i in row) + GAP)
        if row and side == "B":
            bot_lim = min(bot_lim, min(i["cy"] - i["h"] / 2 for i in row) - GAP)
    for side in "LR":
        col = sorted([i for i in items if i["side"] == side], key=lambda i: i["py"])
        for i in col:
            if side == "L":
                i["cx"] = max(bx0 - 24 - i["w"] / 2, i["w"] / 2 + 28)
            else:
                i["cx"] = min(bx1 + 24 + i["w"] / 2, W - i["w"] / 2 - 28)
            i["cy"] = i["py"]
        y = top_lim
        for i in col:
            i["cy"] = max(i["cy"], y + i["h"] / 2)
            y = i["cy"] + i["h"] / 2 + GAP
        y = bot_lim
        for i in reversed(col):
            i["cy"] = min(i["cy"], y - i["h"] / 2)
            y = i["cy"] - i["h"] / 2 - GAP


def _assign(items, bento_px, W, H, title_bottom):
    """横書きで外側に入らなければ縦書きを試し、それでもダメなら写真の上に重ねる。"""
    bx0, by0, bx1, by1 = bento_px
    room = {"L": bx0, "R": W - bx1, "T": by0 - title_bottom, "B": H - by1}
    used = {k: 0.0 for k in room}
    cap = {"L": H - title_bottom - 30, "R": H - title_bottom - 30, "T": W - 60, "B": W - 60}

    def dist(i, s):
        return {"L": i["px"] - bx0, "R": bx1 - i["px"], "T": i["py"] - by0, "B": by1 - i["py"]}[s]

    def dims(i, vert):
        return (i["vw"], i["vh"]) if vert else (i["hw"], i["hh"])

    def fits(i, s, vert):
        w, h = dims(i, vert)
        size = w + 36 if s in "LR" else h + 30
        need = h + GAP if s in "LR" else w + 34
        return room[s] >= size and used[s] + need <= cap[s]

    # 置き場所の指定（place: T/B/L/R）があるものを先に決める
    order = sorted(items, key=lambda i: (i.get("place") not in room, min(dist(i, s) for s in room)))
    for i in order:
        want = i.get("place") if i.get("place") in room else None
        sides = [want] if want else sorted(room, key=lambda s: dist(i, s))
        choice = next(((s, False) for s in sides if fits(i, s, False)), None) \
            or next(((s, True) for s in sides if s in "LR" and i["can_vert"] and fits(i, s, True)), None)
        if not choice and want:                    # 指定の場所に入らなくても、そこに重ねて置く
            choice = (want, False)
        if choice:
            i["side"], i["vertical"] = choice
            i["inside"] = False
        else:
            i["side"] = "L" if i["px"] < (bx0 + bx1) / 2 else "R"
            i["vertical"], i["inside"] = False, True
        i["w"], i["h"] = dims(i, i["vertical"])
        used[i["side"]] += (i["h"] + GAP) if i["side"] in "LR" else (i["w"] + 34)


def render(img: Image.Image, title: str, dishes_px: list[dict], bento_px: tuple,
           variant: dict, date=None, seed: int = 0, show_notes: bool = False,
           uniform_ink: bool = True) -> Image.Image:
    """dishes_px: [{name, note, px, py, place?}]（出力画像のピクセル座標）
    show_notes: おかず名の下に（補足）を書くか / uniform_ink: 文字色を全部白にそろえるか"""
    rng = random.Random(seed)
    W, H = img.size
    base = img.convert("RGBA")
    f_label, f_note = _pen(variant, 54), _pen(variant, 36)
    bx0, by0, bx1, by1 = bento_px

    # ---- タイトル（長ければ小さく）
    t_text = _title(title, variant.get("title_style", "kakko"), date)
    size = 88
    while size > 50 and _pen(variant, size).length(t_text) * SP > W - 240:
        size -= 4
    f_title = _pen(variant, size)
    tw, th = _measure([t_text], f_title)
    t_center = (W / 2, 34 + th / 2)
    title_bottom = 34 + th + 12

    # ---- ラベルの大きさ（横書き・縦書きの両方を測っておく）
    items = []
    for d in dishes_px:
        name = d["name"]
        h_lines = _wrap(name, 6)
        hw, hh = _measure(h_lines, f_label)
        note = f"({d['note']})" if d.get("note") and show_notes else ""
        n_lines = _wrap(note, 8) if note else []
        if n_lines:
            nw, nh = _measure(n_lines, f_note)
            hw, hh = max(hw, nw), hh + nh + 2
        vw, vh = _measure([name], f_label, vertical=True)
        if note:
            nvw, nvh = _measure([note], f_note, vertical=True)
            vw, vh = vw + nvw, max(vh, nvh)
        items.append({**d, "h_lines": h_lines, "n_lines": n_lines, "note": note,
                      "hw": hw, "hh": hh, "vw": vw, "vh": vh,
                      "can_vert": len(name) <= 7 and len(note) <= 10})
    _assign(items, bento_px, W, H, title_bottom)
    _resolve(items, W, H, title_bottom, bento_px)

    # ---- 描画
    layers = {WHITE: Image.new("RGBA", base.size, (0, 0, 0, 0)),
              DARK: Image.new("RGBA", base.size, (0, 0, 0, 0))}
    t_box = (t_center[0] - tw / 2, t_center[1] - th / 2, t_center[0] + tw / 2, t_center[1] + th / 2)
    ink = WHITE if uniform_ink else _ink_for(img, t_box)
    _draw_lines(layers[ink], [t_text], f_title, t_center, ink, rng)
    if variant.get("title_style", "kakko") == "kakko":
        _slashes(ImageDraw.Draw(layers[ink]), t_box, ink, rng)

    for i in items:
        box = (i["cx"] - i["w"] / 2, i["cy"] - i["h"] / 2, i["cx"] + i["w"] / 2, i["cy"] + i["h"] / 2)
        ink = WHITE if uniform_ink else _ink_for(img, box)
        # ラベルを別の透明画像に書いて、全体を少し傾けて貼る
        pad = 40
        tile = Image.new("RGBA", (int(i["w"]) + pad * 2, int(i["h"]) + pad * 2), (0, 0, 0, 0))
        tcx, tcy = tile.width / 2, tile.height / 2
        if i["vertical"]:
            name_w = f_label.size * VW
            _draw_lines(tile, [i["name"]], f_label, (tcx + i["w"] / 2 - name_w / 2, tcy), ink, rng, True)
            if i["note"]:
                note_w = f_note.size * VW
                _draw_lines(tile, [i["note"]], f_note, (tcx - i["w"] / 2 + note_w / 2,
                            tcy - i["h"] / 2 + _measure([i["note"]], f_note, True)[1] / 2 + f_label.size * 0.6),
                            ink, rng, True)
        else:
            name_h = _measure(i["h_lines"], f_label)[1]
            _draw_lines(tile, i["h_lines"], f_label, (tcx, tcy - i["h"] / 2 + name_h / 2), ink, rng)
            if i["n_lines"]:
                nh = _measure(i["n_lines"], f_note)[1]
                _draw_lines(tile, i["n_lines"], f_note, (tcx, tcy + i["h"] / 2 - nh / 2), ink, rng)
        tile = tile.rotate(rng.uniform(-4, 4), resample=Image.BICUBIC, expand=True)
        layers[ink].alpha_composite(tile, (int(i["cx"] - tile.width / 2), int(i["cy"] - tile.height / 2)))

        # 引き出し線：ラベルのおかず側の端 → おかずの手前
        ex, ey = i["px"], i["py"]
        sx = min(max(ex, box[0]), box[2])
        sy = min(max(ey, box[1]), box[3])
        dist = math.hypot(ex - sx, ey - sy)
        if dist > 70:
            ux, uy = (ex - sx) / dist, (ey - sy) / dist
            sx, sy = sx + ux * 14, sy + uy * 14
            ex, ey = ex - ux * 26, ey - uy * 26
            bend = rng.uniform(0.18, 0.3) * rng.choice((-1, 1))
            _swoosh(ImageDraw.Draw(layers[ink]), (sx, sy), (ex, ey), bend, ink, rng)

    out = _with_shadow(base, layers[WHITE], glow=(0, 0, 0))
    out = _with_shadow(out, layers[DARK], glow=(255, 255, 255))
    return out.convert("RGB")
