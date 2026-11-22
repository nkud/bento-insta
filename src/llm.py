"""Claude にツール（JSON の型）で答えてもらう共通処理。

呼び出し方は2通り。どちらも同じ call_tool() で使える。
- CLAUDE_CODE_OAUTH_TOKEN があれば Claude Code CLI 経由（Pro/Max のサブスク枠。API クレジット不要）
- なければ ANTHROPIC_API_KEY で Claude API を直接呼ぶ（従量課金）
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess


def _hint(tool: dict) -> str:
    return f"\n\n必ず {tool['name']} ツールを1回呼び出して答えてください。"


def _with_hint(content, text: str):
    if isinstance(content, str):
        return content + text
    return list(content) + [{"type": "text", "text": text}]


def _json_in(text: str):
    m = re.search(r"\{.*\}", text or "", re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    return None


# ---------- Claude Code CLI（サブスク枠） ----------

def _cli_model(model: str) -> str:
    return os.environ.get("CLAUDE_CLI_MODEL") or model


def _call_cli(model: str, tool: dict, content, timeout: int = 300) -> dict:
    exe = shutil.which("claude")
    if not exe:
        raise RuntimeError("claude コマンドが見つかりません（npm i -g @anthropic-ai/claude-code）")
    blocks = [{"type": "text", "text": content}] if isinstance(content, str) else list(content)
    blocks.append({"type": "text", "text": (
        f"\n\n{tool.get('description', '')}\n"
        "指定の JSON スキーマに合う形で答えてください。ファイル操作やコマンドは使わないでください。")})
    msg = json.dumps({"type": "user", "message": {"role": "user", "content": blocks}}, ensure_ascii=False)
    cmd = [exe, "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
           "--model", _cli_model(model), "--tools", "", "--max-turns", "3",
           "--setting-sources", "", "--json-schema", json.dumps(tool["input_schema"], ensure_ascii=False),
           "--system-prompt", "あなたはお弁当インスタ運用の補助をするアシスタントです。日本語で、指定の形式どおりに答えます。"]
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}   # API 課金に流れないように
    proc = subprocess.run(cmd, input=msg + "\n", capture_output=True, text=True, timeout=timeout, env=env)
    result = None
    texts = []
    for line in proc.stdout.splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("type") == "result":
            result = ev
        elif ev.get("type") == "assistant":
            for b in ev.get("message", {}).get("content", []):
                if b.get("type") == "text":
                    texts.append(b["text"])
    if result is None:
        raise RuntimeError(f"Claude Code CLI が結果を返しませんでした: {(proc.stderr or proc.stdout)[-300:]}")
    if result.get("is_error"):
        raise RuntimeError(f"Claude Code CLI エラー: {str(result.get('result') or result.get('subtype'))[:300]}")
    out = result.get("structured_output")
    if isinstance(out, dict):
        return out
    out = _json_in(result.get("result", "")) or _json_in("\n".join(texts))
    if isinstance(out, dict):
        return out
    raise RuntimeError(f"Claude Code CLI から {tool['name']} の結果を得られませんでした")


# ---------- Claude API（従量課金） ----------

def _call_api(model: str, tool: dict, content, max_tokens: int) -> dict:
    import anthropic
    client = anthropic.Anthropic()
    msgs = [{"role": "user", "content": _with_hint(content, _hint(tool))}]
    for _ in range(3):
        res = client.messages.create(model=model, max_tokens=max_tokens, tools=[tool],
                                     tool_choice={"type": "auto"}, messages=msgs)
        for b in res.content:
            if b.type == "tool_use" and b.name == tool["name"]:
                return b.input
        out = _json_in("".join(getattr(b, "text", "") for b in res.content))   # 念のため本文の JSON も拾う
        if isinstance(out, dict):
            return out
    raise RuntimeError(f"Claude から {tool['name']} の結果を得られませんでした")


def available() -> bool:
    return bool(os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY"))


def call_tool(model: str, tool: dict, content, max_tokens: int = 3000) -> dict:
    if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        last = None
        for _ in range(2):
            try:
                return _call_cli(model, tool, content)
            except RuntimeError as e:
                last = e
                if "limit" in str(e).lower():     # 利用上限なら即やめて後で再試行
                    break
        raise last
    return _call_api(model, tool, content, max_tokens)
