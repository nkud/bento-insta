"""2枚目用：写真におかず名を手書き風に書き込む（引き出し線つき）。"""
from __future__ import annotations

import math
import random

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageStat

from common import FONTS

FONT_FILES = {
    "yomogi": "Yomogi-Regular.ttf",
    "klee": "KleeOne-SemiBold.ttf",
    "kurenaido": "ZenKurenaido-Regular.ttf",
}
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
def _font(name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONTS / FONT_FILES.get(name, FONT_FILES["yomogi"])), size)


def _wrap(text: str, n: int) -> list[str]:
    """日本語を折り返す。n+2 文字までは1行、それ以上は行の長さが揃うよう均等に分ける。
    例: にんじんしりしり → にんじん / しりしり"""
    if len(text) <= n + 2:
        return [text]
    k = -(-len(text) // n)                 # 必要な行数
    size = -(-len(text) // k)              # 1行あたりの文字数（均等）
    return [text[i:i + size] for i in range(0, len(text), size)]


def _measure(lines: list[str], font) -> tuple[float, float, float]:
    lh = font.size * 1.18
    return max(font.getlength(l) for l in lines), lh * len(lines), lh


def _draw_hand_text(layer: Image.Image, lines, font, center, fill, rng):
    """1文字ずつ少し傾け・上下に揺らして、手書きっぽく描く。"""
    tw, th, lh = _measure(lines, font)
    cx, cy = center
    y = cy - th / 2
    for line in lines:
        x = cx - font.getlength(line) / 2
        for ch in line:
            adv = font.getlength(ch)
            pad = int(font.size * 0.4)
            tile = Image.new("RGBA", (int(adv) + pad * 2, int(lh) + pad * 2), (0, 0, 0, 0))
            ImageDraw.Draw(tile).text((pad, pad), ch, font=font, fill=fill)
            tile = tile.rotate(rng.uniform(-6, 6), resample=Image.BICUBIC)
            layer.alpha_composite(tile, (int(x - pad), int(y - pad + rng.uniform(-3, 3))))
            x += adv * rng.uniform(0.96, 1.03)
        y += lh


def _with_shadow(base: Image.Image, layer: Image.Image, glow=(0, 0, 0)) -> Image.Image:
    """文字・線のまわりに柔らかい影を付けて、どんな背景でも読めるようにする。"""
    a = layer.getchannel("A")
    out = base
    for radius, strength in ((10, 0.45), (3, 0.55)):
        sh = Image.new("RGBA", base.size, glow + (0,))
        sh.putalpha(a.filter(ImageFilter.GaussianBlur(radius)).point(lambda v: int(v * strength)))
        out = Image.alpha_composite(out, sh)
    return Image.alpha_composite(out, layer)


# ---------------------------------------------------------------- lines
def _hand_curve(draw, p0, p1, bend, width, fill, rng):
    (x0, y0), (x1, y1) = p0, p1
    dx, dy = x1 - x0, y1 - y0
    dist = math.hypot(dx, dy) or 1
    # 垂直方向に少しふくらませた二次ベジェ
    mx, my = (x0 + x1) / 2 - dy / dist * bend * dist, (y0 + y1) / 2 + dx / dist * bend * dist
    pts = []
    for i in range(41):
        t = i / 40
        x = (1 - t) ** 2 * x0 + 2 * (1 - t) * t * mx + t ** 2 * x1
        y = (1 - t) ** 2 * y0 + 2 * (1 - t) * t * my + t ** 2 * y1
        pts.append((x + rng.uniform(-0.6, 0.6), y + rng.uniform(-0.6, 0.6)))
    draw.line(pts, fill=fill, width=width, joint="curve")
    # 先端に小さな「く」の字の矢じり
    ex, ey = pts[-1]
    px, py = pts[-6]
    ang = math.atan2(ey - py, ex - px)
    for s in (+1, -1):
        a = ang + math.pi + s * math.radians(28)
        draw.line([(ex, ey), (ex + 22 * math.cos(a), ey + 22 * math.sin(a))], fill=fill, width=width)


# ---------------------------------------------------------------- layout
def _title_text(title: str, style: str, date) -> str:
    if style == "kakko":
        return f"＼ {title} ／"
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


def _assign_sides(items, bento_px, W, H, title_bottom):
    """各おかずのラベルを、弁当箱の外側（左・右・上・下）のうち近くて入る場所へ。
    どこにも入らない（弁当箱が画面いっぱい）場合は左右の端に重ねて置く。"""
    bx0, by0, bx1, by1 = bento_px
    room = {"L": bx0, "R": W - bx1, "T": by0 - title_bottom, "B": H - by1}
    used = {k: 0.0 for k in room}
    cap = {"L": H - title_bottom - 30, "R": H - title_bottom - 30, "T": W - 60, "B": W - 60}

    def dist(i, s):
        return {"L": i["px"] - bx0, "R": bx1 - i["px"], "T": i["py"] - by0, "B": by1 - i["py"]}[s]

    def need(i, s):
        return i["h"] + GAP if s in "LR" else i["w"] + 34

    def fits(i, s):
        size = i["w"] + 40 if s in "LR" else i["h"] + 30
        return room[s] >= size and used[s] + need(i, s) <= cap[s]

    # 外周に近いおかずから順に決める
    for i in sorted(items, key=lambda i: min(dist(i, s) for s in room)):
        sides = sorted(room, key=lambda s: dist(i, s))
        ok = [s for s in sides if fits(i, s)]
        if ok:
            i["side"], i["inside"] = ok[0], False
        else:
            lr = [s for s in sides if s in "LR" and used[s] + need(i, s) <= cap[s]]
            i["side"], i["inside"] = (lr or ["L" if i["px"] < (bx0 + bx1) / 2 else "R"])[0], True
        used[i["side"]] += need(i, i["side"])


def _resolve(items, W, H, title_bottom, bento_px):
    """同じ辺に並ぶラベル同士が重ならないよう押し広げる。"""
    bx0, by0, bx1, by1 = bento_px
    for side in "LR":
        col = sorted([i for i in items if i["side"] == side], key=lambda i: i["py"])
        for i in col:
            if side == "L":
                i["cx"] = max(bx0 - 24 - i["w"] / 2, i["w"] / 2 + 28)
            else:
                i["cx"] = min(bx1 + 24 + i["w"] / 2, W - i["w"] / 2 - 28)
            i["cy"] = i["py"]
        y = title_bottom + 10
        for i in col:
            i["cy"] = max(i["cy"], y + i["h"] / 2)
            y = i["cy"] + i["h"] / 2 + GAP
        y = H - 30
        for i in reversed(col):
            i["cy"] = min(i["cy"], y - i["h"] / 2)
            y = i["cy"] - i["h"] / 2 - GAP
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


def render(img: Image.Image, title: str, dishes_px: list[dict], bento_px: tuple,
           variant: dict, date=None, seed: int = 0) -> Image.Image:
    """dishes_px: [{name, note, px, py}]（出力画像のピクセル座標）"""
    rng = random.Random(seed)
    W, H = img.size
    base = img.convert("RGBA")
    fname = variant.get("font", "yomogi")
    f_title, f_label, f_note = _font(fname, 74), _font(fname, 48), _font(fname, 34)
    bx0, by0, bx1, by1 = bento_px

    # ---- タイトル
    t_text = _title_text(title, variant.get("title_style", "kakko"), date)
    t_lines = _wrap(t_text, 13)
    tw, th, _ = _measure(t_lines, f_title)
    t_center = (W / 2, 40 + th / 2)
    title_bottom = 40 + th + 10

    # ---- ラベル配置（左右の余白に振り分け → 縦の重なりを解消）
    items = []
    for d in dishes_px:
        lines = _wrap(d["name"], 6)
        lw, lh_total, _ = _measure(lines, f_label)
        note_lines = []
        if d.get("note"):
            note_lines = _wrap(f"({d['note']})", 8)
            nw, nh, _ = _measure(note_lines, f_note)
            lw, lh_total = max(lw, nw), lh_total + nh + 4
        items.append({**d, "lines": lines, "note_lines": note_lines, "w": lw, "h": lh_total})
    _assign_sides(items, bento_px, W, H, title_bottom)
    _resolve(items, W, H, title_bottom, bento_px)

    # ---- 描画（白インク層と焦げ茶インク層を分けて、それぞれに合う影を付ける）
    layers = {WHITE: Image.new("RGBA", base.size, (0, 0, 0, 0)),
              DARK: Image.new("RGBA", base.size, (0, 0, 0, 0))}

    ink = _ink_for(img, (t_center[0] - tw / 2, t_center[1] - th / 2, t_center[0] + tw / 2, t_center[1] + th / 2))
    _draw_hand_text(layers[ink], t_lines, f_title, t_center, ink, rng)

    for i in items:
        box = (i["cx"] - i["w"] / 2, i["cy"] - i["h"] / 2, i["cx"] + i["w"] / 2, i["cy"] + i["h"] / 2)
        ink = _ink_for(img, box)
        layer = layers[ink]
        name_h = _measure(i["lines"], f_label)[1]
        _draw_hand_text(layer, i["lines"], f_label, (i["cx"], box[1] + name_h / 2), ink, rng)
        if i["note_lines"]:
            nh = _measure(i["note_lines"], f_note)[1]
            _draw_hand_text(layer, i["note_lines"], f_note, (i["cx"], box[3] - nh / 2), ink, rng)
        # 引き出し線：ラベルの内側の端 → おかず
        side = i["side"]
        if side == "L":
            sx, sy = box[2] + 10, box[1] + name_h / 2
        elif side == "R":
            sx, sy = box[0] - 10, box[1] + name_h / 2
        elif side == "T":
            sx, sy = i["cx"], box[3] + 8
        else:
            sx, sy = i["cx"], box[1] - 8
        ex, ey = i["px"], i["py"]
        dist = math.hypot(ex - sx, ey - sy)
        if dist > 60:
            k = (dist - 30) / dist          # おかずの手前で止める
            ex, ey = sx + (ex - sx) * k, sy + (ey - sy) * k
            bend = rng.uniform(0.12, 0.2) * (1 if side in ("L", "B") else -1)
            _hand_curve(ImageDraw.Draw(layer), (sx, sy), (ex, ey), bend, 4, ink, rng)

    out = _with_shadow(base, layers[WHITE], glow=(0, 0, 0))
    out = _with_shadow(out, layers[DARK], glow=(255, 255, 255))
    return out.convert("RGB")
