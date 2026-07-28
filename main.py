"""Modular RAG MCP Server 的启动入口。"""

from pathlib import Path

from core.settings import Settings, load_settings
from observability.logger import get_logger

# 基于入口文件定位配置，避免启动结果依赖调用者当前所在的目录。
DEFAULT_SETTINGS_PATH = Path(__file__).parent / "config" / "settings.yaml"


def main(settings_path: str = str(DEFAULT_SETTINGS_PATH)) -> Settings:
    """启动应用前加载并校验配置。"""
    settings = load_settings(settings_path)
    get_logger(__name__).info("Configuration loaded from %s", settings_path)
    return settings


if __name__ == "__main__":
    main()
