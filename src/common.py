"""共通ユーティリティ：設定・状態ファイル・時刻。"""
from __future__ import annotations

import datetime as dt
import json
import os
import pathlib

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = ROOT / "out"
FONTS = ROOT / "fonts"
JST = dt.timezone(dt.timedelta(hours=9))

POSTS_FILE = DATA / "posts.json"          # 投稿履歴＋指標
STRATEGY_FILE = DATA / "strategy.json"    # 分析から学んだ方針
ACCOUNT_FILE = DATA / "account.json"      # フォロワー数の推移


def load_config() -> dict:
    return yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))


def load_json(path: pathlib.Path, default):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def save_json(path: pathlib.Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def now_jst() -> dt.datetime:
    return dt.datetime.now(JST)


def parse_ts(s: str) -> dt.datetime:
    # Instagram は "2026-11-21T02:00:00+0000" 形式
    if s.endswith("+0000"):
        s = s[:-5] + "+00:00"
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(JST)


def env(name: str, default: str | None = None, required: bool = False) -> str | None:
    v = os.environ.get(name, default)
    if required and not v:
        raise SystemExit(f"環境変数 {name} が設定されていません（GitHub Secrets を確認）")
    return v
