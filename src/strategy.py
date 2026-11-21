"""どの「上げ方」を使うか決める（ε-greedy：基本は勝ちパターン、一部は新しいパターンを試す）。"""
from __future__ import annotations

import random

from common import STRATEGY_FILE, load_json


def load() -> dict:
    return load_json(STRATEGY_FILE, {})


def choose_variant(cfg: dict, strategy: dict, rng: random.Random | None = None) -> dict:
    rng = rng or random.Random()
    exp = cfg["experiment"]
    arms_stats = strategy.get("arms", {})
    variant = {}
    for dim, options in exp["dimensions"].items():
        stats = arms_stats.get(dim, {})
        counts = {str(o): stats.get(str(o), {}).get("n", 0) for o in options}
        under = [o for o in options if counts[str(o)] < exp["min_samples_per_arm"]]
        if under:                                   # まだ試行が足りないものを優先
            pick = rng.choice(under)
        elif rng.random() < exp["epsilon"]:         # 探索
            pick = rng.choice(options)
        else:                                       # 活用：平均スコア最大
            pick = max(options, key=lambda o: stats.get(str(o), {}).get("mean", 0))
        variant[dim] = pick
    return variant


def allowed_hours(cfg: dict, strategy: dict) -> list[int]:
    hours = cfg["posting"]["allowed_hours_jst"]
    learned = strategy.get("insights", {}).get("best_posting_hours") or []
    if cfg["posting"].get("use_learned_hours") and strategy.get("learning_active") and learned:
        return sorted({int(h) for h in learned})
    return hours
