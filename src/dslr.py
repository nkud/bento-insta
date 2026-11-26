"""1枚目を「一眼レフで撮ったような」写真に寄せる（料理そのものは描き換えない）。

やること:
  0. 弁当箱の中だけ「美味しそう」に：立体感・つや・色の鮮やかさ・暖かみを控えめに上げる。
  1. 弁当箱を AI で切り抜き（rembg / isnet）、外側だけをレンズのボケ（円形ボケ）でぼかす。
     弁当箱から離れるほど強くボケるので、被写界深度が浅いレンズのように見える。
  2. 明暗をフィルムのようなトーンカーブに（ハイライトがなめらかに飛ぶ・黒が締まる）。
  3. 周辺を少しだけ暗く（周辺減光）、ごく薄い粒子感。
切り抜きに失敗したら（弁当箱が小さすぎる・大きすぎる）、ボケは付けずトーンだけ整える。
"""
from __future__ import annotations

import cv2
import numpy as np
from PIL import Image

_SESSION = None


def subject_mask(img: Image.Image) -> np.ndarray | None:
    """弁当箱のマスク（0〜1）。切り抜けなければ None。"""
    global _SESSION
    try:
        from rembg import new_session, remove
    except ImportError:
        return None
    if _SESSION is None:
        _SESSION = new_session("isnet-general-use")
    m = np.asarray(remove(img.convert("RGB"), session=_SESSION, only_mask=True), dtype=np.float32) / 255
    area = (m > 0.5).mean()
    if not 0.12 < area < 0.85:          # 弁当箱らしくない切り抜きは使わない
        return None
    return m


def _disc(r: int) -> np.ndarray:
    k = np.zeros((2 * r + 1, 2 * r + 1), np.float32)
    cv2.circle(k, (r, r), r, 1, -1, lineType=cv2.LINE_AA)
    return k / k.sum()


def _lens_blur(a: np.ndarray, keep: np.ndarray, r: int) -> np.ndarray:
    """円形ボケ。弁当箱の色が背景ににじまないよう、背景だけで平均する（正規化畳み込み）。"""
    k = _disc(r)
    w = (1 - keep)[..., None]
    lin = np.clip(a / 255, 0, 1) ** 2.2                      # 光の量でぼかすと明るい点が玉ボケらしくなる
    num = cv2.filter2D(lin * w, -1, k, borderType=cv2.BORDER_REFLECT)
    den = cv2.filter2D((1 - keep), -1, k, borderType=cv2.BORDER_REFLECT)[..., None]
    out = num / np.maximum(den, 1e-3)
    return 255 * np.clip(out, 0, 1) ** (1 / 2.2)


def _tone(a: np.ndarray, cfg: dict) -> np.ndarray:
    x = np.clip(a / 255, 0, 1)
    # フィルム風 S カーブ：中間はそのまま、ハイライトはなめらかに丸め、シャドウを少し締める
    s = cfg.get("contrast", 0.18)
    y = x + s * (x - 0.5) * (1 - np.abs(2 * x - 1)) * 2 * 0.5
    knee = 0.82
    hi = y > knee
    y[hi] = knee + (1 - knee) * np.tanh((y[hi] - knee) / (1 - knee) * 1.2) / np.tanh(1.2)
    return y * 255


def _vignette(a: np.ndarray, strength: float) -> np.ndarray:
    h, w = a.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    d = np.sqrt(((xx - w / 2) / (w / 2)) ** 2 + ((yy - h / 2) / (h / 2)) ** 2) / np.sqrt(2)
    return a * (1 - strength * np.clip(d - 0.35, 0, 1) ** 1.6 / 0.65 ** 1.6)[..., None]


def _grain(a: np.ndarray, amount: float, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    n = cv2.GaussianBlur(rng.normal(0, 1, a.shape[:2]).astype(np.float32), (0, 0), 0.7)
    lum = a.mean(axis=2, keepdims=True) / 255
    return a + amount * 255 * n[..., None] * (0.5 + 0.5 * (1 - np.abs(2 * lum - 1)))


def food_pop(a: np.ndarray, m: np.ndarray, strength: float = 1.0) -> np.ndarray:
    """弁当箱の中だけ：立体感（局所コントラスト）・つや・色の鮮やかさ・暖かみ。"""
    inner = cv2.erode((m > 0.5).astype(np.uint8), np.ones((9, 9), np.uint8))
    w = cv2.GaussianBlur(inner.astype(np.float32), (0, 0), 6)[..., None]
    x = a / 255
    y = x + 0.22 * strength * (x - cv2.GaussianBlur(x, (0, 0), 12))          # 立体感
    y = y + 0.18 * strength * (y - cv2.GaussianBlur(y, (0, 0), 1.2))         # 細部
    hsv = cv2.cvtColor(np.clip(y, 0, 1).astype(np.float32), cv2.COLOR_RGB2HSV)
    sat = hsv[..., 1]
    hsv[..., 1] = np.clip(sat + 0.24 * strength * sat * (1 - sat), 0, 1)        # 鮮やかさ（鮮やかな色は控えめ）
    y = cv2.cvtColor(hsv, cv2.COLOR_HSV2RGB)
    lum = y.mean(axis=2, keepdims=True)
    y = y + 0.07 * strength * np.clip((lum - 0.6) / 0.4, 0, 1) * (1 - y)     # つや
    y = y * (1 + strength * np.array([0.05, 0.02, -0.02]))                   # 暖かみ・少し明るく
    return (x * (1 - w) + np.clip(y, 0, 1) * w) * 255


def enhance(img: Image.Image, cfg: dict | None = None, mask: np.ndarray | None = None) -> Image.Image:
    cfg = cfg or {}
    a = np.asarray(img.convert("RGB"), dtype=np.float32)
    h, w = a.shape[:2]
    if mask is None and (cfg.get("bokeh", True) or cfg.get("food_pop", 1.0)):
        mask = subject_mask(img)
    if mask is not None and cfg.get("food_pop", 1.0):
        a = np.clip(food_pop(a, mask, cfg.get("food_pop", 1.0)), 0, 255)
    if mask is not None and cfg.get("bokeh", True):
        keep = cv2.GaussianBlur(mask, (0, 0), 1.5)
        # 弁当箱からの距離（画像の短辺比）。離れるほど強くボケる
        dist = cv2.distanceTransform(((keep < 0.5) * 255).astype(np.uint8), cv2.DIST_L2, 5) / min(h, w)
        r_max = max(4, int(min(h, w) * cfg.get("bokeh_radius", 0.022)))
        levels = [max(2, r_max // 3), max(3, (2 * r_max) // 3), r_max]
        blurred = [a] + [_lens_blur(a, keep, r) for r in levels]
        t = np.clip(dist / cfg.get("bokeh_ramp", 0.12), 0, 1) * len(levels)   # 0〜3 の連続値
        out = np.zeros_like(a)
        for i in range(len(levels)):
            wgt = np.clip(1 - np.abs(t - (i + 1)), 0, 1) if i < len(levels) - 1 else np.clip(t - i, 0, 1)
            out += blurred[i + 1] * wgt[..., None]
        base = np.clip(1 - t, 0, 1)
        out += a * base[..., None]
        # 境目：弁当箱の縁は元の画素、外側は1段目のボケへなめらかに
        edge = cv2.GaussianBlur(keep, (0, 0), 2.5)[..., None]
        a = a * edge + out * (1 - edge)
    a = _tone(a, cfg)
    a = _vignette(a, cfg.get("vignette", 0.22))
    a = _grain(a, cfg.get("grain", 0.012))
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))
