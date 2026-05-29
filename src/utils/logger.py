"""
日志配置模块

提供统一的日志配置，支持控制台输出和文件输出。
"""

import os
import sys
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime

# 日志目录
LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "logs")
os.makedirs(LOG_DIR, exist_ok=True)

# 日志格式
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logging(
    level=logging.INFO,
    console=True,
    file=True,
    max_file_size=10 * 1024 * 1024,  # 10MB
    backup_count=5
):
    """
    配置日志系统

    Args:
        level: 日志级别
        console: 是否输出到控制台
        file: 是否输出到文件
        max_file_size: 日志文件最大大小（字节）
        backup_count: 备份文件数量
    """
    # 创建根logger
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # 清除现有的处理器
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # 控制台处理器
    if console:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(level)
        console_formatter = logging.Formatter(LOG_FORMAT, DATE_FORMAT)
        console_handler.setFormatter(console_formatter)
        root_logger.addHandler(console_handler)

    # 文件处理器
    if file:
        log_file = os.path.join(LOG_DIR, f"chat_{datetime.now().strftime('%Y%m%d')}.log")
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=max_file_size,
            backupCount=backup_count,
            encoding="utf-8"
        )
        file_handler.setLevel(level)
        file_formatter = logging.Formatter(LOG_FORMAT, DATE_FORMAT)
        file_handler.setFormatter(file_formatter)
        root_logger.addHandler(file_handler)

    # 设置第三方库的日志级别
    logging.getLogger("PySide6").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """
    获取指定名称的logger

    Args:
        name: logger名称

    Returns:
        logging.Logger实例
    """
    return logging.getLogger(name)


# 示例使用
if __name__ == "__main__":
    setup_logging(level=logging.DEBUG)
    logger = get_logger(__name__)
    logger.debug("调试信息")
    logger.info("一般信息")
    logger.warning("警告信息")
    logger.error("错误信息")
