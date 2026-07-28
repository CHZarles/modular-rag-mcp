"""应用日志辅助函数。"""

import logging


def get_logger(name: str) -> logging.Logger:
    """返回向标准错误流输出的 Logger。"""
    logger = logging.getLogger(name)
    # 多次启动或重复获取同名 Logger 时，避免叠加重复的 stderr Handler。
    if not logger.handlers:
        logger.addHandler(logging.StreamHandler())
        logger.propagate = False
    logger.setLevel(logging.INFO)
    return logger
