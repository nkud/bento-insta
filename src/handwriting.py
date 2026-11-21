"""文字を「書き順どおりの線」で描いて、指やペンで書いたような手書き文字にする。

フォントの輪郭を塗るのではなく、KanjiVG（漢字・かなの書き順データ, CC BY-SA 3.0）の
1画ずつの中心線を、丸いペンでなぞって描きます。画ごと・文字ごとに位置や角度を少し揺らすので、
毎回すこしずつ違う「その場で書いた字」になります。データに無い文字はフォントで代用します。
"""
from __future__ import annotations

import functools
import gzip
import json
import math
import re

from PIL import Image, ImageDraw, ImageFont

from common import FONTS

BOX = 109.0                         # KanjiVG の座標系（109×109）
SMALL = set("ぁぃぅぇぉっゃゅょゎァィゥェォッャュョヮヵヶ")

# KanjiVG に無い記号は、ここで線を定義する
EXTRA = {
    "(": ["M64,8 c-20,24 -20,69 0,93"], "（": ["M64,8 c-20,24 -20,69 0,93"],
    ")": ["M45,8 c20,24 20,69 0,93"], "）": ["M45,8 c20,24 20,69 0,93"],
    "×": ["M32,34 L77,79", "M77,34 L32,79"],
    "〜": ["M14,60 c12,-18 26,-18 40,0 s28,18 41,0"], "～": ["M14,60 c12,-18 26,-18 40,0 s28,18 41,0"],
    "・": ["M52,52 l4,4"],
    "-": ["M30,56 L79,56"],
}


@functools.lru_cache(maxsize=1)
def _data() -> dict:
    with gzip.open(FONTS / "kanjivg-strokes.json.gz", "rt", encoding="utf-8") as f:
        d = json.load(f)
    d.update(EXTRA)
    return d


def supports(ch: str) -> bool:
    return ch in _data()


# ---------------------------------------------------------------- SVG パス → 点列
_TOK = re.compile(r"[MmCcSsLlZz]|-?\d*\.?\d+(?:e-?\d+)?")


def _bez(p0, p1, p2, p3, n=10):
    return [((1 - t) ** 3 * p0[0] + 3 * (1 - t) ** 2 * t * p1[0] + 3 * (1 - t) * t ** 2 * p2[0] + t ** 3 * p3[0],
             (1 - t) ** 3 * p0[1] + 3 * (1 - t) ** 2 * t * p1[1] + 3 * (1 - t) * t ** 2 * p2[1] + t ** 3 * p3[1])
            for t in [i / n for i in range(1, n + 1)]]


@functools.lru_cache(maxsize=4096)
def strokes(ch: str) -> tuple:
    """1文字を「画ごとの点列」のタプルにする（109×109 座標）。"""
    out = []
    for d in _data().get(ch, []):
        toks = _TOK.findall(d)
        i, cmd, cur, start, last_c2 = 0, None, (0.0, 0.0), (0.0, 0.0), None
        pts: list = []

        def num():
            nonlocal i
            v = float(toks[i])
            i += 1
            return v
        while i < len(toks):
            if re.fullmatch(r"[A-Za-z]", toks[i]):
                cmd = toks[i]
                i += 1
                if cmd in "Zz":
                    pts.append(start)
                    continue
            rel = cmd.islower()
            ox, oy = cur if rel else (0.0, 0.0)
            c = cmd.upper()
            if c == "M":
                cur = (ox + num(), oy + num())
                start = cur
                pts.append(cur)
                cmd = "l" if rel else "L"       # 続く座標は直線扱い
                last_c2 = None
            elif c == "L":
                cur = (ox + num(), oy + num())
                pts.append(cur)
                last_c2 = None
            elif c == "C":
                c1 = (ox + num(), oy + num())
                c2 = (ox + num(), oy + num())
                p = (ox + num(), oy + num())
                pts += _bez(cur, c1, c2, p)
                cur, last_c2 = p, c2
            elif c == "S":
                c1 = (2 * cur[0] - last_c2[0], 2 * cur[1] - last_c2[1]) if last_c2 else cur
                c2 = (ox + num(), oy + num())
                p = (ox + num(), oy + num())
                pts += _bez(cur, c1, c2, p)
                cur, last_c2 = p, c2
            else:
                i += 1
        if len(pts) >= 2:
            out.append(tuple(pts))
    return tuple(out)


def _bbox(ch):
    xs = [x for s in strokes(ch) for x, _ in s]
    ys = [y for s in strokes(ch) for _, y in s]
    return (min(xs), min(ys), max(xs), max(ys)) if xs else (0, 0, BOX, BOX)


def advance(ch: str, size: float) -> float:
    """1文字ぶんの送り幅。全角はほぼ正方形、英数字や記号は字の幅に合わせて詰める。"""
    if ch == " ":
        return size * 0.4
    if ch in SMALL:
        return size * 0.78
    if ord(ch) < 0x3000 or ch in "（）()":
        x0, _, x1, _ = _bbox(ch)
        return max((x1 - x0) / BOX * size * 0.9 + size * 0.22, size * 0.4)
    return size * 0.9


# ---------------------------------------------------------------- 描画
def char_tile(ch: str, size: int, fill, rng, pen: float = 0.085, slant: float = -0.12) -> Image.Image:
    """1文字の手書き画像（透明背景, size×size の正方形 + 余白）。2倍で描いて縮小し、線をなめらかに。"""
    S = 2
    pad = int(size * 0.25)
    W = size + pad * 2
    im = Image.new("RGBA", (W * S, W * S), (0, 0, 0, 0))
    dr = ImageDraw.Draw(im)
    ss = strokes(ch)
    x0, _, x1, _ = _bbox(ch)
    narrow = ord(ch) < 0x3000 or ch in "（）()"
    cx_off = (BOX / 2 - (x0 + x1) / 2) if narrow else 0       # 幅の狭い字は中央に寄せる
    k = size / BOX * 0.92
    rot = math.radians(rng.uniform(-4, 4))
    cs, sn = math.cos(rot), math.sin(rot)
    width = max(2, round(size * pen * S * rng.uniform(0.92, 1.08)))
    for st in ss:
        dx, dy = rng.uniform(-2.2, 2.2), rng.uniform(-2.2, 2.2)       # 画ごとのズレ
        wob_a, wob_f, wob_p = rng.uniform(0.4, 1.2), rng.uniform(0.05, 0.12), rng.uniform(0, 6.3)
        pts = []
        for j, (x, y) in enumerate(st):
            x, y = x + cx_off + dx - BOX / 2, y + dy - BOX / 2
            x += y * slant                                              # 少し右に傾ける
            w = wob_a * math.sin(j * wob_f * 6 + wob_p)                 # ゆらぎ
            x, y = x + w, y + w * 0.6
            xr, yr = x * cs - y * sn, x * sn + y * cs
            pts.append(((xr * k + W / 2) * S, (yr * k + W / 2) * S))
        dr.line(pts, fill=fill, width=width, joint="curve")
        r = width / 2
        for px, py in (pts[0], pts[-1]):
            dr.ellipse((px - r, py - r, px + r, py + r), fill=fill)
    return im.resize((W, W), Image.LANCZOS)


class Pen:
    """手書き（書き順データ）で描き、データに無い字だけフォントで描くペン。"""

    def __init__(self, size: int, fallback_font: str, pen: float = 0.085):
        self.size = size
        self.pen = pen
        self.font = ImageFont.truetype(str(FONTS / fallback_font), size)

    def advance(self, ch: str) -> float:
        return advance(ch, self.size) if supports(ch) else self.font.getlength(ch)

    def length(self, text: str) -> float:
        return sum(self.advance(c) for c in text)

    def tile(self, ch: str, fill, rng, rot90: bool = False) -> Image.Image:
        if supports(ch):
            t = char_tile(ch, self.size, fill, rng, pen=self.pen)
        else:
            pad = int(self.size * 0.4)
            adv = self.font.getlength(ch)
            t = Image.new("RGBA", (int(max(adv, self.size)) + pad * 2, int(self.size * 1.3) + pad * 2), (0, 0, 0, 0))
            ImageDraw.Draw(t).text((pad + (max(adv, self.size) - adv) / 2, pad), ch, font=self.font, fill=fill)
            t = t.rotate(rng.uniform(-4, 4), resample=Image.BICUBIC, expand=True)
        if rot90:
            t = t.rotate(-90, resample=Image.BICUBIC, expand=True)
        return t


class FontPen:
    """手書き風フォントで1文字ずつ描くペン（Pen と同じ使い方）。stroke で線を少し太らせる。"""

    def __init__(self, size: int, font_file: str, stroke: int = 1):
        self.size = size
        self.stroke = stroke
        self.font = ImageFont.truetype(str(FONTS / font_file), size)

    def advance(self, ch: str) -> float:
        return self.font.getlength(ch)

    def length(self, text: str) -> float:
        return sum(self.advance(c) for c in text)

    def tile(self, ch: str, fill, rng, rot90: bool = False) -> Image.Image:
        pad = int(self.size * 0.4)
        adv = self.font.getlength(ch)
        t = Image.new("RGBA", (int(max(adv, self.size)) + pad * 2, int(self.size * 1.3) + pad * 2), (0, 0, 0, 0))
        ImageDraw.Draw(t).text((pad + (max(adv, self.size) - adv) / 2, pad), ch, font=self.font, fill=fill,
                               stroke_width=self.stroke, stroke_fill=fill)
        t = t.rotate(rng.uniform(-5, 5) + (-90 if rot90 else 0), resample=Image.BICUBIC, expand=True)
        return t
