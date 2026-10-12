"""配置加载：config.yaml + 环境变量覆盖。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml


def load_config(path: str | Path) -> dict[str, Any]:
    """读取 YAML 配置并应用环境变量覆盖。

    覆盖项：
      GHTREND_GITHUB_TOKEN  -> enrich token（不写入配置，单独取用）
      GHTREND_LLM_BASE_URL  -> llm.base_url
      GHTREND_LLM_API_KEY   -> llm.api_key
      GHTREND_LLM_MODEL     -> llm.model
    """
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    llm = cfg.setdefault("llm", {})
    if os.environ.get("GHTREND_LLM_BASE_URL"):
        llm["base_url"] = os.environ["GHTREND_LLM_BASE_URL"]
    if os.environ.get("GHTREND_LLM_MODEL"):
        llm["model"] = os.environ["GHTREND_LLM_MODEL"]
    llm["api_key"] = os.environ.get("GHTREND_LLM_API_KEY", "")
    return cfg


def github_token() -> str:
    return os.environ.get("GHTREND_GITHUB_TOKEN", "")
