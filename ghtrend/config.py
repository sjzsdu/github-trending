"""配置加载：config.yaml + 环境变量覆盖。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

# LLM key 的环境变量回退链（GHTREND_LLM_API_KEY 优先）；key 永不落盘
LLM_KEY_ENV_CHAIN = [
    "GHTREND_LLM_API_KEY",
    "DEEPSEEK_API_KEY",
    "DASHSCOPE_API_KEY",
    "OPENAI_API_KEY",
]


def load_config(path: str | Path) -> dict[str, Any]:
    """读取 YAML 配置并应用环境变量覆盖（base_url / model / api_key）。"""
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    llm = cfg.setdefault("llm", {})
    if os.environ.get("GHTREND_LLM_BASE_URL"):
        llm["base_url"] = os.environ["GHTREND_LLM_BASE_URL"]
    if os.environ.get("GHTREND_LLM_MODEL"):
        llm["model"] = os.environ["GHTREND_LLM_MODEL"]
    return cfg


def resolve_llm_key(cfg: dict[str, Any]) -> str:
    """按回退链解析 LLM key；找不到返回 ''（调用方负责 fail-fast 报错）。"""
    for name in LLM_KEY_ENV_CHAIN:
        if os.environ.get(name):
            return os.environ[name]
    return ""


def require_llm_key(cfg: dict[str, Any]) -> str:
    """真实生成路径必须拿到 key，否则给出可操作的报错。"""
    key = resolve_llm_key(cfg)
    if not key:
        raise RuntimeError(
            "未配置 LLM API key：请设置 GHTREND_LLM_API_KEY，"
            "或导出 DEEPSEEK_API_KEY / DASHSCOPE_API_KEY / OPENAI_API_KEY 之一。"
            "离线测试请使用 --mock-llm。"
        )
    return key


def github_token() -> str:
    return os.environ.get("GHTREND_GITHUB_TOKEN", "")
