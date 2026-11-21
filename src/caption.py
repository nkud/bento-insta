"""キャプション（内容・ひとことコメント・ハッシュタグ）を Claude で作る。"""
from __future__ import annotations

import re

from llm import call_tool

TOOL = {
    "name": "write_caption",
    "description": "Instagram のお弁当投稿キャプションを返す",
    "input_schema": {
        "type": "object",
        "properties": {
            "comment": {"type": "string", "description": "ひとことコメント（本文）"},
            "hashtags": {"type": "array", "items": {"type": "string"},
                         "description": "# 付きのハッシュタグ。重複なし"},
        },
        "required": ["comment", "hashtags"],
    },
}

PROMPT = """あなたはお弁当アカウントの中の人です。下の情報から Instagram 投稿のコメントとハッシュタグを作ってください。

# 今日のお弁当
お弁当名: {title}
おかず: {dishes}
弁当箱: {box}
見どころ: {highlights}
作った人のメモ: {memo}

# 書き方
- 口調: {voice}
- コメントの長さ: {length}
- メモがあれば、その内容（エピソード）を必ず自然に入れる。メモに無い事実は作らない。\n{memo_slide}
- ハッシュタグは必ず含めるタグを含めてちょうど {n_tags} 個（Instagram の上限は5個）。残りは今日のおかずや内容に合う具体的なタグにする。
- 必ず含めるタグ: {base_tags}
{learned}"""

LENGTH = {
    "short": "1〜2文（40〜70文字程度）",
    "long": "3〜5文（120〜200文字程度）。作り方のちょっとしたコツや工夫を1つ入れる",
}


def _learned_block(strategy: dict) -> str:
    ins = strategy.get("insights") or {}
    if not strategy.get("learning_active") or not ins:
        return ""
    parts = ["# これまでの投稿データから分かったこと（反映してください）"]
    for t in ins.get("caption_tips", [])[:5]:
        parts.append(f"- {t}")
    if ins.get("recommended_hashtags"):
        parts.append(f"- 伸びやすいタグ（合うものを優先）: {' '.join(ins['recommended_hashtags'][:15])}")
    if ins.get("avoid_hashtags"):
        parts.append(f"- 効果が薄いタグ（使わない）: {' '.join(ins['avoid_hashtags'][:15])}")
    return "\n".join(parts)


def _norm_tag(t: str) -> str:
    t = re.sub(r"\s+", "", t.strip())
    return t if t.startswith("#") else "#" + t


def build(analysis: dict, memo: str, variant: dict, strategy: dict, cfg: dict) -> str:
    cap = cfg["caption"]
    n_tags = min(int(variant.get("hashtag_count", 5)), 5)
    base = [_norm_tag(t) for t in cap["base_hashtags"]]
    out = call_tool(cfg["claude"]["model"], TOOL, PROMPT.format(
            title=analysis["title"],
            dishes="、".join(d["name"] for d in analysis["dishes"]),
            box=analysis.get("box", ""),
            highlights="、".join(analysis.get("highlights", [])),
            memo=memo or "（なし）",
            voice=cap["voice"],
            length=LENGTH.get(variant.get("caption_length", "short"), LENGTH["short"]),
            n_tags=n_tags,
            base_tags=" ".join(base),
            learned=_learned_block(strategy),
            memo_slide=("- 3枚目に作り置きの段取りメモの画像がある。保存したくなるよう、最後に『段取りは3枚目にメモしました』のような一言を自然に入れる。" if cfg["image"].get("memo_slide") else ""),
    ), max_tokens=1500)

    tags = []
    for t in base + [_norm_tag(t) for t in out["hashtags"]]:
        if t not in tags and len(t) > 1:
            tags.append(t)
    tags = tags[:min(max(n_tags, len(base)), 5)]   # Instagram は1投稿5個まで

    lines = [analysis["title"]]
    lines += [f"☆{d['name']}" for d in analysis["dishes"]]
    lines += ["", out["comment"].strip()]
    if cap.get("signature"):
        lines += ["", cap["signature"]]
    lines += ["", "\n".join(tags)]
    text = "\n".join(lines)
    return text[:2200]


def hashtags_of(caption: str) -> list[str]:
    return re.findall(r"#[^\s#]+", caption)

