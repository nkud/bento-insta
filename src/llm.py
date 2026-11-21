"""Claude にツール（JSON の型）で答えてもらう共通処理。"""
from __future__ import annotations

import json
import re

import anthropic


def call_tool(model: str, tool: dict, content, max_tokens: int = 3000) -> dict:
    """tool_choice を強制できないモデルでも動くよう、auto＋指示＋再試行で確実に JSON を得る。"""
    client = anthropic.Anthropic()
    msgs = [{"role": "user", "content": content}]
    hint = f"\n\n必ず {tool['name']} ツールを1回呼び出して答えてください。"
    if isinstance(content, str):
        msgs[0]["content"] = content + hint
    else:
        msgs[0]["content"] = list(content) + [{"type": "text", "text": hint}]
    for _ in range(3):
        res = client.messages.create(model=model, max_tokens=max_tokens, tools=[tool],
                                     tool_choice={"type": "auto"}, messages=msgs)
        for b in res.content:
            if b.type == "tool_use" and b.name == tool["name"]:
                return b.input
        text = "".join(getattr(b, "text", "") for b in res.content)
        m = re.search(r"\{.*\}", text, re.S)      # 念のため本文の JSON も拾う
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
    raise RuntimeError(f"Claude から {tool['name']} の結果を得られませんでした")
