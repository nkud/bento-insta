"""キャプション（内容・ひとことコメント・ハッシュタグ）を Claude で作る。"""
from __future__ import annotations

import re

import anthropic

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
- メモがあれば、その内容（エピソード）を必ず自然に入れる。メモに無い事実は作らない。
- ハッシュタグはちょうど {n_tags} 個。おかず名・弁当箱・季節・お弁当系の定番を混ぜる。
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
    n_tags = int(variant.get("hashtag_count", 15))
    base = [_norm_tag(t) for t in cap["base_hashtags"]]
    client = anthropic.Anthropic()
    res = client.messages.create(
        model=cfg["claude"]["model"],
        max_tokens=1500,
        tools=[TOOL],
        tool_choice={"type": "tool", "name": "write_caption"},
        messages=[{"role": "user", "content": PROMPT.format(
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
        )}],
    )
    out = next(b.input for b in res.content if b.type == "tool_use")

    tags = []
    for t in base + [_norm_tag(t) for t in out["hashtags"]]:
        if t not in tags and len(t) > 1:
            tags.append(t)
    tags = tags[:min(max(n_tags, len(base)), 30)]   # Instagram は最大30個

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

