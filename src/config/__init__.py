"""
配置模块

提供统一的配置管理接口。
"""

from .config_manager import Config, ConfigManager, get_global_config_manager, get_config

__all__ = [
    "Config",
    "ConfigManager",
    "get_global_config_manager",
    "get_config",
]