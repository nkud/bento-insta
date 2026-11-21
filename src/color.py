"""1枚目の写真の色を整える（外部サービスなし・料理の形は変えない）。

やること（どれも控えめ）:
  1. 白さの補正 … 蛍光灯や電球で黄色・青に寄った色かぶりを半分だけ戻す
  2. 明るさの補正 … 暗い写真を持ち上げ、白飛び・黒つぶれしない範囲でコントラストを整える
  3. 暖かみ … 食べ物がおいしく見えるよう、ほんの少し暖色に
  4. 鮮やかさ … 彩度を少しだけ上げる
  5. くっきり … 輪郭を軽くシャープに
"""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageEnhance, ImageFilter


def _white_balance(a: np.ndarray, strength: float) -> np.ndarray:
    # 明るすぎ・暗すぎる画素を除いた「灰色の平均」で色かぶりを推定（gray world）
    lum = a.mean(axis=2)
    mask = (lum > 30) & (lum < 230)
    if mask.sum() < 1000:
        return a
    means = a[mask].mean(axis=0)
    gray = means.mean()
    gain = 1 + strength * (gray / np.maximum(means, 1) - 1)
    gain = np.clip(gain, 0.85, 1.15)          # 料理本来の色を壊さないよう制限
    return a * gain


def _levels(a: np.ndarray) -> np.ndarray:
    lum = a.mean(axis=2)
    lo, hi = np.percentile(lum, 0.5), np.percentile(lum, 99.5)
    lo, hi = min(lo, 25), max(hi, 215)         # 伸ばしすぎない
    a = (a - lo) * (255 / max(hi - lo, 1))
    # 暗い写真は中間調を持ち上げる（平均明るさが 125 に近づくように、上限つき）
    m = np.clip(a, 0, 255).mean() / 255
    if m < 0.47:
        gamma = np.clip(np.log(0.49) / np.log(max(m, 0.05)), 0.75, 1.0)
        a = 255 * (np.clip(a, 0, 255) / 255) ** gamma
    return a


def enhance(img: Image.Image, cfg: dict | None = None) -> Image.Image:
    cfg = cfg or {}
    if not cfg.get("enabled", True):
        return img
    a = np.asarray(img.convert("RGB")).astype(np.float32)
    a = _white_balance(a, cfg.get("white_balance", 0.5))
    a = _levels(a)
    warm = cfg.get("warmth", 0.03)
    a[..., 0] *= 1 + warm
    a[..., 2] *= 1 - warm
    out = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))
    out = ImageEnhance.Color(out).enhance(cfg.get("saturation", 1.12))
    out = ImageEnhance.Contrast(out).enhance(cfg.get("contrast", 1.04))
    out = out.filter(ImageFilter.UnsharpMask(radius=1.6, percent=cfg.get("sharpen", 55), threshold=3))
    return out
