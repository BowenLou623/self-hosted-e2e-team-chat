"""
应用入口模块

启动聊天应用的主入口点。
处理应用初始化和主事件循环。
"""

import sys
import os
import logging
import argparse

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer

from src.ui.main_window import MainWindow
from src.ui.login_dialog import show_login_dialog
from src.utils.logger import setup_logging
from src.transport import get_transport
from src.config.config_manager import ConfigManager
from src.identity.local_identity import IdentityManager
from src.identity.key_store import KeyStore
from src.identity.pairing import PairingManager
from src.storage.sqlite_store import SQLiteStore
from src.app.launch_ticket import LaunchTicketError, LaunchTicketStore
from src.app.launcher_events import emit_launcher_event
from src.app.runtime_paths import resolve_runtime_paths


def parse_args(argv=None):
    """解析命令行参数"""
    parser = argparse.ArgumentParser(description="即时聊天系统")
    parser.add_argument("--profile", type=str,
                        help="本地配置档名称，只用于隔离 data/config/chat.db，不是登录ID或 user_id")
    parser.add_argument("--user-id", type=str, 
                        help="兼容/高级参数：首次初始化时指定固定 user_id；已有 identity 时不可覆盖")
    parser.add_argument("--user", type=str, 
                        help="向后兼容参数：同 --user-id (不推荐使用)")
    parser.add_argument("--display-name", type=str,
                        help="用户显示名称 (可选，留空时由 UI 回退显示 user-id)")
    parser.add_argument("--transport", type=str, default="memory",
                        choices=["memory", "network"],
                        help="传输模式: memory(内存) 或 network(网络) (默认: memory)")
    parser.add_argument("--hub", type=str, default="localhost:8080",
                        help="Hub服务器地址 (格式: host:port) (默认: localhost:8080，仅network模式需要)")
    parser.add_argument("--debug", action="store_true",
                        help="启用调试模式")
    parser.add_argument("--config", type=str,
                        help="配置文件路径 (JSON格式)")
    parser.add_argument("--data-dir", type=str,
                        help="数据存储目录 (默认: data)")
    parser.add_argument("--db-path", type=str,
                        help="SQLite 数据库路径 (默认: <data-dir>/chat.db)")
    parser.add_argument("--config-dir", type=str,
                        help="身份与配置目录 (默认: <data-dir>/config)")
    parser.add_argument("--log-level", type=str, choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
                        help="日志级别")
    parser.add_argument("--launch-ticket", type=str,
                        help="macOS Launcher 登录后生成的一次性启动票据")
    return parser.parse_args(argv)


def main():
    """应用主函数"""
    # 解析命令行参数
    args = parse_args()
    
    # 创建配置管理器
    config_manager = ConfigManager(config_file=args.config)
    
    # 用命令行参数更新配置
    args_dict = vars(args)
    config_manager.update_from_args(args_dict)
    
    # 获取最终配置
    config = config_manager.get_config()
    
    # 应用命令行参数中的特定覆盖。--profile 只映射到本地运行目录，不设置 user_id。
    try:
        runtime_paths = resolve_runtime_paths(args, config)
    except ValueError as e:
        print(f"参数错误: {e}", file=sys.stderr)
        return 2

    data_dir = runtime_paths.data_dir
    db_path = runtime_paths.db_path
    config_dir = runtime_paths.config_dir

    config.data_dir = data_dir
    config.db_path = db_path
    if args.log_level:
        config.log_level = args.log_level
    
    # 设置日志
    log_level = config.get_log_level_int()
    setup_logging(level=log_level)
    logger = logging.getLogger(__name__)
    
    # 处理向后兼容的 --user 参数警告
    if args.user and not args.user_id:
        logger.warning(f"参数 --user 已弃用，请使用 --user-id。将使用 '{args.user}' 作为用户ID。")
    if args.user_id:
        logger.warning("参数 --user-id 仅用于兼容/高级初始化；日常启动请使用 --profile 隔离本地配置档。")
    if runtime_paths.profile:
        logger.info(
            f"使用本地 profile: {runtime_paths.profile}，data_dir={data_dir}，config_dir={config_dir}"
        )
    
    # 创建传输层配置
    transport_config = config_manager.get_transport_config()
    
    try:
        transport = get_transport(config.transport_mode, transport_config)
        logger.info(f"传输层创建成功: {transport.get_status()}")
    except Exception as e:
        logger.error(f"创建传输层失败: {e}", exc_info=True)
        return 1

    # 创建Qt应用
    app = QApplication(sys.argv)
    app.setApplicationName("即时聊天系统")
    app.setOrganizationName("内部团队")

    # 为当前客户端显式创建独立依赖，避免退回默认全局路径
    store = SQLiteStore(db_path)
    identity_manager = IdentityManager(config_dir, store=store)
    key_store = KeyStore(store=store)
    pairing_manager = PairingManager(key_store=key_store, identity_manager=identity_manager)

    if args.launch_ticket:
        try:
            ticket = LaunchTicketStore(config_dir).consume(args.launch_ticket, runtime_paths.profile)
            if not identity_manager.load_existing_identity():
                raise LaunchTicketError("无法读取本地 identity")
            current_user = identity_manager.get_current_user()
            if current_user is None:
                raise LaunchTicketError("本地 identity 为空")
            if current_user.user_id != ticket.get("user_id"):
                raise LaunchTicketError("启动票据与本地 identity 不一致")
            user_id = current_user.user_id
            display_name = current_user.display_name
            config.user_id = user_id
            config.display_name = display_name
            logger.info(f"Launcher ticket 登录成功，用户ID: {user_id}，显示名称: {display_name}")
            emit_launcher_event("login_ok", user_id=user_id, display_name=display_name, profile=runtime_paths.profile)
        except LaunchTicketError as e:
            logger.error(f"Launcher ticket 登录失败: {e}")
            emit_launcher_event("fatal_error", stage="launch_ticket", error=str(e), profile=runtime_paths.profile)
            return 1
    else:
        try:
            user_id, display_name = show_login_dialog(
                config_dir,
                identity_manager=identity_manager,
                preferred_user_id=config.user_id or None,
                parent=None,
            )
            config.user_id = user_id
            config.display_name = display_name
            logger.info(f"登录成功，用户ID: {user_id}，显示名称: {display_name}")
            emit_launcher_event("login_ok", user_id=user_id, display_name=display_name, profile=runtime_paths.profile)
        except ValueError as e:
            logger.error(f"登录取消或失败: {e}")
            emit_launcher_event("fatal_error", stage="login", error=str(e), profile=runtime_paths.profile)
            return 1
    
    logger.info(f"启动即时聊天系统，用户ID: {config.user_id}，显示名称: {config.display_name}，传输模式: {config.transport_mode}")
    if config.transport_mode == "network":
        logger.info(f"网络模式，Hub地址: {config.hub_address}")
    if config.enable_debug:
        logger.debug("调试模式已启用")

    # 创建主窗口
    try:
        window = MainWindow(
            current_user_id=config.user_id,
            transport=transport,
            data_dir=data_dir,
            db_path=db_path,
            config_dir=config_dir,
            display_name=config.display_name,
            store=store,
            identity_manager=identity_manager,
            key_store=key_store,
            pairing_manager=pairing_manager,
        )
        window.show()
        logger.info("主窗口创建成功")
        emit_launcher_event("main_window_ready", user_id=config.user_id, profile=runtime_paths.profile)

        # 启动应用事件循环
        exit_code = app.exec()
        logger.info(f"应用退出，返回码: {exit_code}")
        emit_launcher_event("app_exited", exit_code=exit_code, profile=runtime_paths.profile)
        return exit_code

    except Exception as e:
        logger.error(f"应用启动失败: {e}", exc_info=True)
        emit_launcher_event("fatal_error", stage="main_window", error=str(e), profile=runtime_paths.profile)
        return 1


if __name__ == "__main__":
    sys.exit(main())
