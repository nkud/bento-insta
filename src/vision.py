"""Claude で写真を解析：お弁当名・おかず名・おかずの位置・弁当箱の範囲など。"""
from __future__ import annotations

import base64
import io

from PIL import Image

from llm import call_tool

TOOL = {
    "name": "report_bento",
    "description": "お弁当写真の解析結果を返す",
    "input_schema": {
        "type": "object",
        "properties": {
            "is_bento": {"type": "boolean", "description": "お弁当の写真か"},
            "title": {"type": "string",
                      "description": "主役のおかずから付けたお弁当名。例: 牛しぐれ煮弁当 / 鮭マヨ弁当 / 茶弁当"},
            "bento_bbox": {
                "type": "object",
                "description": "弁当箱全体を囲む矩形。画像の幅・高さに対する 0〜1 の比率",
                "properties": {k: {"type": "number"} for k in ("x0", "y0", "x1", "y1")},
                "required": ["x0", "y0", "x1", "y1"],
            },
            "dishes": {
                "type": "array",
                "description": "写っているおかず・ご飯（3〜7品。小さな飾りのミニトマト等は省いてよい）",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "おかず名（8文字程度まで）。例: カボチャサラダ"},
                        "note": {"type": "string", "description": "補足（任意・12文字まで）。例: まいたけとエノキ"},
                        "x": {"type": "number", "description": "そのおかずの中心の x（0〜1）"},
                        "y": {"type": "number", "description": "そのおかずの中心の y（0〜1）"},
                    },
                    "required": ["name", "x", "y"],
                },
            },
            "main": {"type": "string", "enum": ["meat", "fish", "egg", "tofu_veg", "rice_dish", "noodle", "other"],
                     "description": "主菜の種類"},
            "tone": {"type": "string", "enum": ["brown", "colorful", "green", "white", "other"],
                     "description": "全体の色味（茶色弁当＝brown）"},
            "box": {"type": "string", "description": "弁当箱の種類。例: 曲げわっぱ / 2段 / プラ容器"},
            "highlights": {"type": "array", "items": {"type": "string"},
                           "description": "キャプションに使える見どころ（彩り・半熟卵など）2〜4個"},
            "prep": {
                "type": "object",
                "description": "3枚目の「作り置きメモ」用。共働きの家庭で無理なく作る段取りとして自然な振り分け",
                "properties": {
                    "night": {"type": "array", "items": {"type": "string"}, "description": "前の晩に作っておくおかず"},
                    "morning": {"type": "array", "items": {"type": "string"}, "description": "朝に作る・焼く・詰めるだけのおかず"},
                    "stock": {"type": "array", "description": "作り置きに向くおかず（0〜3品）",
                              "items": {"type": "object", "properties": {
                                  "name": {"type": "string"},
                                  "keep": {"type": "string", "description": "日持ちの目安。控えめに。例: 冷蔵2日 / 冷凍2週間"}},
                                  "required": ["name", "keep"]}},
                    "tip": {"type": "string", "description": "時短や詰め方のコツを1〜2文（60文字以内）"},
                },
                "required": ["night", "morning", "stock", "tip"],
            },
        },
        "required": ["is_bento", "title", "bento_bbox", "dishes", "main", "tone", "box", "highlights", "prep"],
    },
}

PROMPT = """この写真はお弁当の写真です。Instagram 投稿用に解析してください。
- おかず名は家庭料理として自然な日本語で（推測でOK、迷ったら一般的な名前）。
- x, y はそのおかずが写っている部分の中心を、画像全体に対する比率で正確に。
- 投稿者のメモがあれば、おかず名やお弁当名、作り置きの段取りはメモを優先してください。\n- prep の日持ちは家庭料理として安全側（短め）の目安にしてください。ご飯やおかずの全品をどれかに振り分ける必要はありません。
投稿者のメモ: {memo}"""


def _to_jpeg_b64(img: Image.Image, max_side: int = 1568) -> str:
    im = img.copy()
    im.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    im.convert("RGB").save(buf, "JPEG", quality=88)
    return base64.standard_b64encode(buf.getvalue()).decode()


def analyze(img: Image.Image, memo: str, model: str) -> dict:
    data = call_tool(model, TOOL, [
        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": _to_jpeg_b64(img)}},
        {"type": "text", "text": PROMPT.format(memo=memo or "（なし）")},
    ], max_tokens=2000)
    # 値の範囲を安全側に丸める
    for d in data["dishes"]:
        d["x"] = min(max(float(d["x"]), 0.0), 1.0)
        d["y"] = min(max(float(d["y"]), 0.0), 1.0)
    return data
