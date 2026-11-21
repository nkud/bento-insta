"""3枚目用：「作り置きメモ」を、ノートに手書きしたような1枚にする。

中身は写真の解析（vision.py の prep）から:
  前の晩に作るもの / 朝に作るもの / 作り置き（日持ちの目安つき）/ ひとことポイント
"""
from __future__ import annotations

import math
import random

from PIL import Image, ImageDraw, ImageFilter, ImageOps

import handwriting

PAPER = (251, 246, 236)
INK = (62, 46, 36, 255)
ACCENT = (190, 98, 52, 255)
GRID = (226, 216, 198)
FONT = "Yomogi-Regular.ttf"


def _pen(size: int, stroke: int = 1):
    return handwriting.FontPen(size, FONT, stroke)


def _write(layer: Image.Image, text: str, pen, x: float, y: float, fill, rng) -> float:
    """左寄せで1行書く。戻り値は書き終わりの x。"""
    for ch in text:
        adv = pen.advance(ch) * 1.03
        t = pen.tile(ch, fill, rng)
        layer.alpha_composite(t, (int(x + adv / 2 - t.width / 2), int(y + pen.size * 0.62 - t.height / 2 + rng.uniform(-2, 2))))
        x += adv * rng.uniform(0.97, 1.02)
    return x


def _wrap(text: str, pen, width: float) -> list[str]:
    lines, cur = [], ""
    for ch in text:
        if pen.length(cur + ch) * 1.03 > width and cur:
            lines.append(cur)
            cur = ch
        else:
            cur += ch
    if cur:
        lines.append(cur)
    return lines


def _wavy(draw, x0, x1, y, fill, rng, width=4):
    pts = [(x, y + 3 * math.sin((x - x0) / 14) + rng.uniform(-0.6, 0.6)) for x in range(int(x0), int(x1), 4)]
    draw.line(pts, fill=fill, width=width, joint="curve")


def _paper(W: int, H: int) -> Image.Image:
    im = Image.new("RGB", (W, H), PAPER)
    d = ImageDraw.Draw(im)
    for y in range(140, H - 60, 56):                 # ノートの罫線（点線）
        for x in range(60, W - 60, 12):
            d.point([(x, y), (x + 1, y)], fill=GRID)
    noise = Image.effect_noise((W, H), 10).convert("L").filter(ImageFilter.GaussianBlur(1))
    return Image.composite(im, Image.new("RGB", (W, H), (240, 233, 220)), ImageOps.autocontrast(noise).point(lambda v: 255 - v // 8))


def _polaroid(photo: Image.Image, size: int, rng) -> Image.Image:
    """写真を白フチ＋マスキングテープ風にして少し傾ける。"""
    p = ImageOps.fit(photo, (size, int(size * 1.15)))
    pad = 14
    card = Image.new("RGBA", (p.width + pad * 2, p.height + pad * 2 + 18), (255, 255, 255, 255))
    card.paste(p, (pad, pad))
    tape = Image.new("RGBA", (int(size * 0.45), 34), (235, 200, 150, 170))
    card.alpha_composite(tape, ((card.width - tape.width) // 2, 0))
    shadow = Image.new("RGBA", (card.width + 30, card.height + 30), (0, 0, 0, 0))
    shadow.paste(Image.new("RGBA", card.size, (0, 0, 0, 60)), (18, 20))
    shadow = shadow.filter(ImageFilter.GaussianBlur(8))
    shadow.alpha_composite(card, (6, 6))
    return shadow.rotate(rng.uniform(4, 7), resample=Image.BICUBIC, expand=True)


def render(photo: Image.Image, title: str, prep: dict, size=(1080, 1350), seed: int = 0) -> Image.Image:
    """中身が多いときは、下の注意書きに重ならない大きさまで文字を小さくする。"""
    for scale in (1.0, 0.92, 0.85, 0.78, 0.72):
        img, bottom = _render(photo, title, prep, size, seed, scale)
        if bottom <= size[1] - 120:
            return img
    return img


def _render(photo, title, prep, size, seed, k):
    rng = random.Random(seed)
    W, H = size
    base = _paper(W, H).convert("RGBA")
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)

    # 右上に写真
    pol = _polaroid(photo, 300, rng)
    base.alpha_composite(pol, (W - pol.width - 40, 40))

    left, right = 80, W - 80
    text_right = W - pol.width - 60          # 写真の横は狭い

    h1 = _pen(80, 2)
    _write(layer, "作り置きメモ", h1, left, 70, INK, rng)
    _wavy(draw, left, left + h1.length("作り置きメモ") * 1.03, 70 + 98, ACCENT, rng, 5)
    sub = _pen(40, 1)
    y = 200
    for line in _wrap(title, sub, text_right - left):
        _write(layer, line, sub, left + 4, y, INK, rng)
        y += 52

    h2, body, small = _pen(round(50 * k), 2), _pen(round(44 * k), 1), _pen(round(34 * k), 1)
    y = max(y + 30, 300)

    def section(label: str, items: list[tuple[str, str]]):
        nonlocal y
        if not items:
            return
        _write(layer, "■ " + label, h2, left, y, ACCENT, rng)
        y += 74 * k
        for name, note in items:
            width = (text_right if y < pol.height + 60 else right) - left - 40
            for i, line in enumerate(_wrap(name, body, width)):
                if i == 0:
                    _write(layer, "・", body, left + 6, y, INK, rng)
                x_end = _write(layer, line, body, left + 46, y, INK, rng)
                y += 58 * k
            if note:
                nx = x_end + 14
                if nx + small.length(f"（{note}）") > right:
                    nx, y = left + 60, y
                    _write(layer, f"（{note}）", small, nx, y - 6, INK, rng)
                    y += 46 * k
                else:
                    _write(layer, f"（{note}）", small, nx, y - 50 * k, INK, rng)
        y += 22 * k

    section("前の晩に", [(n, "") for n in prep.get("night", [])])
    section("朝に", [(n, "") for n in prep.get("morning", [])])
    section("作り置き", [(s.get("name", ""), s.get("keep", "")) for s in prep.get("stock", [])])

    tip = (prep.get("tip") or "").strip()
    if tip:
        _write(layer, "■ ポイント", h2, left, y, ACCENT, rng)
        y += 74 * k
        for line in _wrap(tip, body, right - left - 20)[:4]:
            _write(layer, line, body, left + 16, y, INK, rng)
            y += 58 * k

    foot = _pen(30, 1)
    _write(layer, "※日持ちは目安です", foot, left, H - 92, INK, rng)
    tag = "#在宅だんな弁当"
    _write(layer, tag, foot, right - foot.length(tag) * 1.03, H - 92, ACCENT, rng)

    return Image.alpha_composite(base, layer).convert("RGB"), y
