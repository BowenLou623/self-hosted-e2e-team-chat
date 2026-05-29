"""
配置管理器

提供统一的配置管理，支持从配置文件、命令行参数和环境变量加载配置。
默认配置值在此定义。
"""

import os
import json
import logging
from dataclasses import dataclass, field, asdict
from typing import Optional, Dict, Any
from pathlib import Path


@dataclass
class Config:
    """应用配置数据类"""
    
    # 用户设置
    user_id: str = ""  # 用户唯一ID，如果为空则自动生成
    display_name: str = ""  # 用户显示名称，如果为空则由UI回退显示 user_id
    transport_mode: str = "memory"  # memory, network
    hub_address: str = "localhost:8080"
    
    # 数据存储
    data_dir: str = "data"
    db_path: str = "data/chat.db"
    
    # 日志设置
    log_level: str = "INFO"
    enable_debug: bool = False
    log_to_file: bool = False
    log_file_path: str = "logs/chat.log"
    
    # 网络设置
    enable_reconnect: bool = True
    reconnect_interval: int = 5  # 秒
    connection_timeout: int = 30  # 秒
    
    # 加密设置
    enable_encryption: bool = True
    encryption_version: str = "aes-256-gcm"
    
    # 解码鲁棒性设置
    decode_robust_mode: bool = False  # 解码鲁棒性增强模式（灰度开关）
    
    # UI设置
    window_width: int = 1200
    window_height: int = 700
    theme: str = "light"
    
    # 扩展字段
    extra: Dict[str, Any] = field(default_factory=dict)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Config":
        """从字典创建配置实例"""
        # 提取已知字段
        known_fields = {f.name for f in cls.__dataclass_fields__.values()}
        known_data = {k: v for k, v in data.items() if k in known_fields}
        extra_data = {k: v for k, v in data.items() if k not in known_fields}
        
        # 创建配置实例
        config = cls(**known_data)
        config.extra = extra_data
        return config
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        result = asdict(self)
        return result
    
    def update_from_args(self, args: Dict[str, Any]) -> None:
        """从参数字典更新配置（例如命令行参数）"""
        # 优先使用user_id参数，其次使用user参数（向后兼容）
        if "user_id" in args and args["user_id"]:
            self.user_id = args["user_id"]
        elif "user" in args and args["user"]:
            self.user_id = args["user"]
        if "display_name" in args and args["display_name"]:
            self.display_name = args["display_name"]
        if "transport" in args:
            self.transport_mode = args["transport"]
        if "hub" in args:
            self.hub_address = args["hub"]
        if "debug" in args and args["debug"]:
            self.enable_debug = True
            self.log_level = "DEBUG"
    
    def get_db_path(self) -> str:
        """获取数据库完整路径"""
        if os.path.isabs(self.db_path):
            return self.db_path
        return os.path.join(self.data_dir, "chat.db")
    
    def get_log_level_int(self) -> int:
        """获取日志级别对应的整数"""
        level_map = {
            "DEBUG": logging.DEBUG,
            "INFO": logging.INFO,
            "WARNING": logging.WARNING,
            "ERROR": logging.ERROR,
            "CRITICAL": logging.CRITICAL,
        }
        return level_map.get(self.log_level.upper(), logging.INFO)


class ConfigManager:
    """配置管理器"""
    
    DEFAULT_CONFIG_PATHS = [
        "config.json",
        "~/.instant-chat/config.json",
        "./instant-messaging-team/config.json",
    ]
    
    def __init__(self, config_file: Optional[str] = None):
        self.config = Config()
        self.config_file = config_file
        self._load_config()
    
    def _load_config(self) -> None:
        """加载配置：首先从配置文件，然后从环境变量"""
        # 1. 从配置文件加载（如果存在）
        config_data = self._load_config_file()
        if config_data:
            self.config = Config.from_dict(config_data)
        
        # 2. 从环境变量更新
        self._update_from_env()
    
    def _load_config_file(self) -> Optional[Dict[str, Any]]:
        """从配置文件加载"""
        config_paths = []
        
        # 如果指定了配置文件，则优先使用
        if self.config_file:
            config_paths.append(self.config_file)
        
        # 添加默认配置文件路径
        config_paths.extend(self.DEFAULT_CONFIG_PATHS)
        
        for path_str in config_paths:
            path = Path(path_str).expanduser().resolve()
            if path.exists() and path.is_file():
                try:
                    with open(path, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    logging.debug(f"从配置文件加载配置: {path}")
                    return data
                except Exception as e:
                    logging.warning(f"加载配置文件失败 {path}: {e}")
        
        return None
    
    def _update_from_env(self) -> None:
        """从环境变量更新配置"""
        env_mapping = {
            "CHAT_USER_ID": "user_id",
            "CHAT_DISPLAY_NAME": "display_name",
            "CHAT_TRANSPORT_MODE": "transport_mode",
            "CHAT_HUB_ADDRESS": "hub_address",
            "CHAT_LOG_LEVEL": "log_level",
            "CHAT_ENABLE_DEBUG": "enable_debug",
            "CHAT_DATA_DIR": "data_dir",
        }
        
        for env_var, config_field in env_mapping.items():
            value = os.getenv(env_var)
            if value is not None:
                # 类型转换
                current_type = type(getattr(self.config, config_field))
                try:
                    if current_type == bool:
                        # 布尔值：支持 true/false, 1/0
                        value = value.lower() in ("true", "1", "yes", "on")
                    elif current_type == int:
                        value = int(value)
                    else:
                        value = str(value)
                    
                    setattr(self.config, config_field, value)
                except (ValueError, TypeError) as e:
                    logging.warning(f"环境变量 {env_var} 值 '{value}' 无法转换为 {current_type}: {e}")
    
    def save_config(self, file_path: Optional[str] = None) -> bool:
        """保存配置到文件"""
        if not file_path:
            if not self.config_file:
                # 使用第一个默认路径
                self.config_file = self.DEFAULT_CONFIG_PATHS[0]
            file_path = self.config_file
        
        try:
            path = Path(file_path).expanduser().resolve()
            path.parent.mkdir(parents=True, exist_ok=True)
            
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(self.config.to_dict(), f, indent=2, ensure_ascii=False)
            
            logging.info(f"配置保存到: {path}")
            return True
        except Exception as e:
            logging.error(f"保存配置失败: {e}")
            return False
    
    def update_from_args(self, args: Dict[str, Any]) -> None:
        """从命令行参数更新配置"""
        self.config.update_from_args(args)
    
    def get_config(self) -> Config:
        """获取当前配置"""
        return self.config
    
    def get_transport_config(self) -> Dict[str, Any]:
        """获取传输层配置字典"""
        config = self.config
        transport_config = {}
        
        if config.transport_mode == "network":
            transport_config["hub_address"] = config.hub_address
        
        transport_config["enable_reconnect"] = config.enable_reconnect
        transport_config["reconnect_interval"] = config.reconnect_interval
        transport_config["connection_timeout"] = config.connection_timeout
        
        return transport_config
    
    def get_logging_config(self) -> Dict[str, Any]:
        """获取日志配置字典"""
        config = self.config
        return {
            "level": config.get_log_level_int(),
            "enable_debug": config.enable_debug,
            "log_to_file": config.log_to_file,
            "log_file_path": config.log_file_path,
        }


# 全局配置管理器实例
_global_config_manager: Optional[ConfigManager] = None

def get_global_config_manager() -> ConfigManager:
    """获取全局配置管理器实例（单例）"""
    global _global_config_manager
    if _global_config_manager is None:
        _global_config_manager = ConfigManager()
    return _global_config_manager

def get_config() -> Config:
    """获取当前配置（快捷函数）"""
    return get_global_config_manager().get_config()
