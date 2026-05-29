"""
主窗口模块

定义了应用的主窗口界面，包含三个主要区域：
1. 联系人面板（左侧）
2. 会话面板（中间）
3. 消息面板（右侧）

使用假数据初始化界面，为后续连接真实业务逻辑预留接口。
"""

import sys
import time
import threading
import subprocess
import html
from typing import Optional, List, Dict, Any
from datetime import datetime
from pathlib import Path

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QSplitter,
    QListWidget, QListWidgetItem, QTextBrowser, QLineEdit, QPushButton,
    QLabel, QFrame, QApplication, QMessageBox, QMenu, QInputDialog,
    QFileDialog, QDialog
)
from PySide6.QtCore import Qt, Signal, Slot, QTimer, QThread, QUrl
from PySide6.QtGui import QFont, QColor, QDesktopServices

# QAction 在 PySide6 中的位置可能不同，尝试从 QtGui 导入，如果失败则从 QtWidgets 导入
try:
    from PySide6.QtGui import QAction
except ImportError:
    from PySide6.QtWidgets import QAction

from src.models.user import User, UserStatus
from src.models.conversation import Conversation, ConversationType
from src.models.message import Message, MessageStatus, MessageAuthStatus
from src.models.contact import ContactAuthStatus
from src.core.events import EventType, Event, subscribe, publish_simple
from src.transport.local_memory import LocalMemoryTransport
from src.transport.interface import Transport
from src.chat.chat_service import ChatService
from src.files.temp_file_service import TEMP_FILE_SCHEMA, TempFileServiceError
from src.utils.logger import get_logger


def run_in_main_thread(func):
    """装饰器，确保函数在主线程执行"""
    from PySide6.QtCore import QTimer
    import functools
    
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        # 获取当前应用实例
        app = QApplication.instance()
        if app is None:
            # 没有应用实例，直接执行（可能在测试环境中）
            return func(*args, **kwargs)
        
        # 检查当前线程是否为主线程
        if QThread.currentThread() == app.thread():
            return func(*args, **kwargs)
        else:
            # 使用QTimer.singleShot在主线程执行
            result = None
            import threading
            event = threading.Event()
            
            def invoke():
                nonlocal result
                try:
                    result = func(*args, **kwargs)
                finally:
                    event.set()
            
            QTimer.singleShot(0, invoke)
            event.wait()
            return result
    
    return wrapper


class ContactsPanel(QFrame):
    """联系人面板，显示联系人列表"""

    contact_selected = Signal(str)  # 信号：联系人被选中，参数为联系人ID
    target_selected = Signal(dict)  # 信号：聊天对象被选中，支持 direct/group

    def __init__(self, chat_service: ChatService, parent=None):
        super().__init__(parent)
        self.chat_service = chat_service
        self.logger = get_logger("contacts_panel")
        self.setFrameStyle(QFrame.Shape.StyledPanel | QFrame.Shadow.Raised)
        self.setMinimumWidth(200)
        self.setMaximumWidth(300)

        self.init_ui()
        self.load_contacts()

    def init_ui(self):
        """初始化UI"""
        layout = QVBoxLayout()

        # 标题
        title = QLabel("会话")
        title.setFont(QFont("Arial", 14, QFont.Weight.Bold))
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        # 搜索和发起聊天区域
        search_layout = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("输入真实 user_id (例如: u_xxxxxxxx)...")
        search_layout.addWidget(self.search_input)
        
        self.start_chat_button = QPushButton("开始聊天")
        self.start_chat_button.clicked.connect(self.start_chat_by_id)
        search_layout.addWidget(self.start_chat_button)
        
        layout.addLayout(search_layout)

        group_layout = QHBoxLayout()
        self.create_group_button = QPushButton("创建群组")
        self.create_group_button.clicked.connect(self.create_group)
        group_layout.addWidget(self.create_group_button)
        layout.addLayout(group_layout)

        # 联系人列表
        self.contacts_list = QListWidget()
        self.contacts_list.itemClicked.connect(self.on_contact_clicked)
        # 设置右键菜单
        self.contacts_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.contacts_list.customContextMenuRequested.connect(self.on_contacts_context_menu)
        layout.addWidget(self.contacts_list)

        self.setLayout(layout)

    def load_contacts(self):
        """从聊天服务加载联系人数据，显示未读计数"""
        # 检查是否在主线程
        from PySide6.QtCore import QThread
        app = QApplication.instance()
        in_main_thread = app and QThread.currentThread() == app.thread()
        self.logger.info(f"开始加载联系人数据 (主线程: {in_main_thread})")
        
        contacts = self.chat_service.get_contacts()
        conversations = self.chat_service.get_conversations()
        self.logger.info(f"获取到 {len(contacts)} 个联系人，{len(conversations)} 个会话")
        
        # 详细日志
        for i, (user_id, user) in enumerate(contacts.items()):
            self.logger.info(f"联系人 {i}: id={user_id}, display_name={user.display_name}, type={type(user).__name__}")
        
        # 计算每个联系人/群组的未读消息总数
        unread_counts = {}
        group_unread_counts = {}
        current_user_id = self.chat_service.get_current_user_id()
        
        for conv_id, conv in conversations.items():
            if conv.conversation_type == ConversationType.GROUP:
                group_unread_counts[conv.id] = conv.unread_count
                continue
            # 找到对方参与者
            for participant_id in conv.participant_ids:
                if participant_id != current_user_id:
                    # 累加未读计数
                    unread_counts[participant_id] = unread_counts.get(participant_id, 0) + conv.unread_count
                    break

        self.contacts_list.clear()
        for user_id, user in contacts.items():
            # 显示名称、状态和未读计数
            display_name = self.chat_service.get_contact_display_label(user_id)
            status_text = user.status.value
            trust_status = getattr(user, "trust_status", ContactAuthStatus.UNKNOWN)
            
            # 添加未读计数（如果有）
            unread_count = unread_counts.get(user_id, 0)
            unread_text = f" [{unread_count}]" if unread_count > 0 else ""

            trust_text = ""
            if trust_status == ContactAuthStatus.PENDING_INCOMING:
                pending_count = getattr(user, "pending_message_count", 0)
                trust_text = f" [待授权{pending_count}]" if pending_count > 0 else " [待授权]"
            elif trust_status == ContactAuthStatus.REJECTED:
                trust_text = " [已拒绝]"

            device_identity = (getattr(user, "metadata", {}) or {}).get("device_identity", {})
            device_name = str(device_identity.get("device_name") or "").strip()
            device_text = f" · {device_name}" if user.status == UserStatus.ONLINE and device_name else ""
            display_text = f"{display_name}{trust_text}{unread_text} ({status_text}{device_text})"
            item = QListWidgetItem(display_text)
            item.setData(Qt.ItemDataRole.UserRole, {
                "kind": "direct",
                "id": user_id,
                "conversation_id": None,
            })
            # 设置工具提示显示用户ID
            item.setToolTip(
                f"contact_id: {user.user_id}\ntrust_status: {trust_status.value}\n"
                f"device_id: {device_identity.get('device_id', '-')}\n"
                f"fingerprint: {device_identity.get('device_fingerprint', '-')}"
            )

            # 根据状态设置颜色
            if user.status == UserStatus.ONLINE:
                item.setForeground(QColor("green"))
            elif user.status == UserStatus.AWAY:
                item.setForeground(QColor("orange"))
            elif user.status == UserStatus.BUSY:
                item.setForeground(QColor("red"))
            elif user.status == UserStatus.OFFLINE:
                item.setForeground(QColor("gray"))
            elif user.status == UserStatus.INVISIBLE:
                item.setForeground(QColor("lightgray"))
                
            # 如果有未读消息，加粗显示
            if unread_count > 0:
                font = item.font()
                font.setBold(True)
                item.setFont(font)

            self.contacts_list.addItem(item)

        groups = self.chat_service.get_groups()
        for group_id, group in groups.items():
            members = self.chat_service.get_group_members(group_id)
            unread_count = group_unread_counts.get(group_id, 0)
            unread_text = f" [{unread_count}]" if unread_count > 0 else ""
            display_text = f"[群] {group.name}{unread_text} ({len(members)}人)"
            item = QListWidgetItem(display_text)
            item.setData(Qt.ItemDataRole.UserRole, {
                "kind": "group",
                "id": group_id,
                "conversation_id": group_id,
            })
            item.setToolTip(
                f"group_id: {group_id}\n成员: {', '.join(member.user_id for member in members)}\n"
                f"文件同步: {self.chat_service.get_group_sync_overview(group_id).get('status', 'unconfigured')}"
            )
            item.setForeground(QColor("darkBlue"))
            if unread_count > 0:
                font = item.font()
                font.setBold(True)
                item.setFont(font)
            self.contacts_list.addItem(item)

    def start_chat_by_id(self):
        """通过用户ID发起聊天"""
        target_id = self.search_input.text().strip()
        if not target_id:
            QMessageBox.warning(self, "警告", "请输入联系人ID")
            return

        try:
            from src.models.user import User, UserStatus

            resolution = self.chat_service.resolve_contact_input(target_id)
            status = resolution.get("status")
            exact_target_id = resolution.get("user_id", "")
            display_name = resolution.get("display_name", "")
            identity_manager = self.chat_service.identity_manager
            self.logger.info(
                f"开始添加联系人: 输入='{target_id}'，解析状态={status}, user_id='{exact_target_id}', "
                f"display_name='{display_name}'，config_dir={identity_manager.config_dir}"
            )

            if status == "invalid":
                if resolution.get("reason") == "whitespace":
                    QMessageBox.warning(self, "无效联系人", "联系人ID不能包含空白字符，请输入真实 user_id")
                else:
                    QMessageBox.warning(self, "无效联系人", "联系人ID不能为空")
                return
            if status == "self":
                QMessageBox.warning(self, "无效联系人", "不能将自己添加为联系人")
                return
            if status == "display_name":
                QMessageBox.warning(
                    self,
                    "用户不存在",
                    f"未找到 user_id “{target_id}”。\n显示名称不能用于发起聊天，请让对方复制顶部“我的身份”里的 ID 给你。",
                )
                return
            if status == "not_found":
                QMessageBox.warning(
                    self,
                    "用户不存在",
                    f"未找到用户 {target_id}。\n请确认对方客户端已启动，并输入真实 user_id。",
                )
                return

            contacts = self.chat_service.get_contacts()
            self.logger.info(f"联系人检查: contact_id='{exact_target_id}'，是否已存在={exact_target_id in contacts}，现有联系人数量={len(contacts)}")
            if exact_target_id in contacts:
                if display_name and contacts[exact_target_id].display_name != display_name:
                    identity_manager.update_contact_display_name(exact_target_id, display_name)
                    self.load_contacts()
                self.target_selected.emit({
                    "kind": "direct",
                    "id": exact_target_id,
                    "conversation_id": None,
                })
                self.contact_selected.emit(exact_target_id)
                return
            
            target_user = User(
                user_id=exact_target_id,
                display_name=display_name,
                original_username=exact_target_id,
                status=UserStatus.ONLINE
            )
            self.logger.info(f"创建用户对象: user_id='{exact_target_id}'，display_name='{display_name}', status=ONLINE")
            
            self.logger.info(
                f"M2-C: 用户主动发起聊天，联系人直接标记为 trusted，contact_id={exact_target_id}"
            )
            success = identity_manager.add_contact(target_user, ContactAuthStatus.TRUSTED)
            self.logger.info(f"添加联系人结果: success={success}, user_id='{exact_target_id}'")
            if success:
                self.chat_service.cleanup_stale_display_name_contact(display_name, exact_target_id)
                self._on_contact_added(exact_target_id)
            else:
                QMessageBox.warning(self, "失败", f"无法添加联系人 {target_id}")
        except Exception as e:
            QMessageBox.warning(self, "错误", f"添加联系人时出错: {str(e)}")

    def _on_contact_added(self, contact_id: str):
        """联系人添加成功后的UI更新"""
        self.logger.info(f"联系人添加成功UI更新: contact_id='{contact_id}'")
        self.search_input.clear()
        self.load_contacts()
        self.target_selected.emit({
            "kind": "direct",
            "id": contact_id,
            "conversation_id": None,
        })
        self.contact_selected.emit(contact_id)

    def on_contact_clicked(self, item):
        """联系人被点击时触发"""
        target = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(target, dict):
            self.target_selected.emit(target)
            if target.get("kind") == "direct":
                self.contact_selected.emit(target.get("id", ""))
            return
        self.target_selected.emit({
            "kind": "direct",
            "id": target,
            "conversation_id": None,
        })
        self.contact_selected.emit(target)

    def create_group(self):
        """创建基础群组。"""
        group_name, ok = QInputDialog.getText(
            self,
            "创建群组",
            "请输入群组名称:",
            QLineEdit.EchoMode.Normal,
            "",
        )
        if not ok:
            return
        group_name = group_name.strip()
        if not group_name:
            QMessageBox.warning(self, "无效群组", "群组名称不能为空")
            return

        member_text, ok = QInputDialog.getText(
            self,
            "添加群成员",
            "请输入已授权联系人 user_id，多个用空格分隔:",
            QLineEdit.EchoMode.Normal,
            "",
        )
        if not ok:
            return
        member_ids = [
            item.strip()
            for item in member_text.split()
            if item.strip()
        ]

        group = self.chat_service.create_group(group_name, member_ids)
        if group is None:
            QMessageBox.warning(self, "创建失败", "请确认群名有效，且成员都是已授权联系人")
            return
        if member_ids:
            QMessageBox.information(self, "邀请已发送", "群组已创建，成员接受邀请后才会加入群聊。")

        self.load_contacts()
        self.target_selected.emit({
            "kind": "group",
            "id": group.id,
            "conversation_id": group.id,
        })
    
    def on_contacts_context_menu(self, position):
        """联系人列表右键菜单"""
        item = self.contacts_list.itemAt(position)
        if not item:
            return
        
        target = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(target, dict):
            if target.get("kind") != "direct":
                return
            contact_id = target.get("id")
        else:
            contact_id = target
        contacts = self.chat_service.get_contacts()
        if contact_id not in contacts:
            return
        
        contact = contacts[contact_id]
        
        # 创建右键菜单
        menu = QMenu(self)
        
        # 修改备注/别名
        edit_display_name_action = menu.addAction("修改显示名")
        edit_display_name_action.triggered.connect(lambda: self.edit_contact_display_name(contact_id))
        
        # 分隔线
        menu.addSeparator()
        
        # 删除联系人
        delete_action = menu.addAction("删除联系人")
        delete_action.triggered.connect(lambda: self.delete_contact(contact_id))
        
        # 显示菜单
        menu.exec(self.contacts_list.mapToGlobal(position))
    
    def edit_contact_display_name(self, contact_id: str):
        """修改联系人显示名。"""
        contacts = self.chat_service.get_contacts()
        if contact_id not in contacts:
            return
        
        contact = contacts[contact_id]
        current_display_name = contact.display_name or ""
        current_name = self.chat_service.get_contact_display_name(contact_id)
        
        # 输入对话框
        from PySide6.QtWidgets import QInputDialog
        new_display_name, ok = QInputDialog.getText(
            self,
            "修改显示名",
            f"为联系人 {current_name} 设置显示名:\n(留空则回退显示 contact_id)",
            QLineEdit.EchoMode.Normal,
            current_display_name
        )
        
        if ok:
            # 通过身份管理器更新联系人
            identity_manager = self.chat_service.identity_manager
            success = identity_manager.update_contact_display_name(contact_id, new_display_name)
            if success:
                QMessageBox.information(self, "成功", "已更新联系人显示名")
                self.load_contacts()
            else:
                QMessageBox.warning(self, "失败", "更新联系人显示名失败")
    
    def delete_contact(self, contact_id: str):
        """删除联系人"""
        contacts = self.chat_service.get_contacts()
        if contact_id not in contacts:
            return
        
        contact = contacts[contact_id]
        contact_name = contact.get_display_name()
        
        reply = QMessageBox.question(
            self,
            "确认删除",
            f"确定要删除联系人 {contact_name} 吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            identity_manager = self.chat_service.identity_manager
            success = identity_manager.remove_contact(contact_id)
            if success:
                QMessageBox.information(self, "成功", f"已删除联系人 {contact_name}")
                self.load_contacts()
            else:
                QMessageBox.warning(self, "失败", "删除联系人失败")





class MessagesPanel(QFrame):
    """消息面板，显示消息历史并发送新消息"""

    message_sent = Signal(str, str)  # 信号：消息发送，参数为消息内容和接收者ID

    def __init__(self, chat_service: ChatService, parent=None):
        super().__init__(parent)
        self.chat_service = chat_service
        self.active_contact_id: Optional[str] = None
        self.active_target: Optional[Dict[str, str]] = None
        self.temp_file_refresh_timer: Optional[QTimer] = None
        self.setFrameStyle(QFrame.Shape.StyledPanel | QFrame.Shadow.Raised)
        self.logger = get_logger("messages_panel")

        self.init_ui()
        self._start_temp_file_refresh_timer()
        # 不再加载模拟消息，等待选择会话

    def init_ui(self):
        """初始化UI"""
        layout = QVBoxLayout()

        # 标题（当前会话）
        self.title_label = QLabel("消息")
        self.title_label.setFont(QFont("Arial", 14, QFont.Weight.Bold))
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.title_label)

        self.auth_hint_label = QLabel("")
        self.auth_hint_label.setWordWrap(True)
        self.auth_hint_label.hide()
        layout.addWidget(self.auth_hint_label)

        self.members_label = QLabel("")
        self.members_label.setWordWrap(True)
        self.members_label.hide()
        layout.addWidget(self.members_label)

        self.sync_status_label = QLabel("")
        self.sync_status_label.setWordWrap(True)
        self.sync_status_label.hide()
        layout.addWidget(self.sync_status_label)

        self.sync_actions_layout = QHBoxLayout()
        self.bind_folder_button = QPushButton("绑定项目文件夹")
        self.bind_folder_button.clicked.connect(self.bind_group_folder)
        self.sync_actions_layout.addWidget(self.bind_folder_button)

        self.syncthing_settings_button = QPushButton("Syncthing设置")
        self.syncthing_settings_button.clicked.connect(self.edit_syncthing_settings)
        self.sync_actions_layout.addWidget(self.syncthing_settings_button)

        self.configure_sync_button = QPushButton("配置Syncthing")
        self.configure_sync_button.clicked.connect(self.configure_syncthing_folder)
        self.sync_actions_layout.addWidget(self.configure_sync_button)

        self.add_sync_device_button = QPushButton("添加同步设备")
        self.add_sync_device_button.clicked.connect(self.add_sync_device)
        self.sync_actions_layout.addWidget(self.add_sync_device_button)

        self.refresh_sync_button = QPushButton("刷新同步")
        self.refresh_sync_button.clicked.connect(self.refresh_sync_status)
        self.sync_actions_layout.addWidget(self.refresh_sync_button)

        self.scan_sync_button = QPushButton("扫描")
        self.scan_sync_button.clicked.connect(self.scan_sync_folder)
        self.sync_actions_layout.addWidget(self.scan_sync_button)

        self.search_project_files_button = QPushButton("搜索文件")
        self.search_project_files_button.clicked.connect(self.search_project_files)
        self.sync_actions_layout.addWidget(self.search_project_files_button)

        self.stop_sync_button = QPushButton("停止同步")
        self.stop_sync_button.clicked.connect(self.stop_syncthing_sync)
        self.sync_actions_layout.addWidget(self.stop_sync_button)

        self.sync_actions_widget = QWidget()
        self.sync_actions_widget.setLayout(self.sync_actions_layout)
        self.sync_actions_widget.hide()
        layout.addWidget(self.sync_actions_widget)

        self.add_group_member_button = QPushButton("添加成员")
        self.add_group_member_button.clicked.connect(self.add_group_members)
        self.add_group_member_button.hide()
        layout.addWidget(self.add_group_member_button)

        # 消息显示区域
        self.messages_display = QTextBrowser()
        self.messages_display.setReadOnly(True)
        self.messages_display.setOpenExternalLinks(False)
        self.messages_display.anchorClicked.connect(self.on_message_link_clicked)
        self.messages_display.setMinimumHeight(400)
        layout.addWidget(self.messages_display)

        # 消息输入区域
        input_layout = QHBoxLayout()

        self.message_input = QLineEdit()
        self.message_input.setPlaceholderText("输入消息...")
        self.message_input.returnPressed.connect(self.send_message)
        input_layout.addWidget(self.message_input)

        self.send_file_button = QPushButton("发送文件")
        self.send_file_button.clicked.connect(self.send_temp_file)
        input_layout.addWidget(self.send_file_button)

        self.send_button = QPushButton("发送")
        self.send_button.clicked.connect(self.send_message)
        input_layout.addWidget(self.send_button)

        layout.addLayout(input_layout)

        self.setLayout(layout)
        self._refresh_send_controls()

    def _refresh_send_controls(self) -> None:
        """根据联系人授权状态更新发送输入区。"""
        if self.active_target and self.active_target.get("kind") == "group":
            self.auth_hint_label.hide()
            self.message_input.setEnabled(True)
            self.send_button.setEnabled(True)
            self.send_file_button.setEnabled(True)
            self.message_input.setPlaceholderText("输入群消息...")
            return

        if not self.active_contact_id:
            self.auth_hint_label.hide()
            self.message_input.setEnabled(False)
            self.send_button.setEnabled(False)
            self.send_file_button.setEnabled(False)
            self.message_input.setPlaceholderText("请先选择联系人")
            return

        trust_status = self.chat_service.get_contact_trust_status(self.active_contact_id)
        block_reason = self.chat_service.get_send_block_reason(self.active_contact_id)
        can_send = block_reason is None

        self.message_input.setEnabled(can_send)
        self.send_button.setEnabled(can_send)
        self.send_file_button.setEnabled(can_send)

        if can_send:
            self.auth_hint_label.hide()
            self.message_input.setPlaceholderText("输入消息...")
            return

        if trust_status == ContactAuthStatus.REJECTED:
            hint_color = "red"
        elif trust_status == ContactAuthStatus.PENDING_INCOMING:
            hint_color = "orange"
        else:
            hint_color = "gray"

        self.auth_hint_label.setText(block_reason or "当前联系人暂不能发送消息")
        self.auth_hint_label.setStyleSheet(f"color: {hint_color};")
        self.auth_hint_label.show()
        self.message_input.setPlaceholderText(block_reason or "当前联系人暂不能发送消息")

    def load_mock_messages(self):
        """加载模拟消息数据（已弃用）"""
        # 第一阶段不再使用模拟消息
        pass

    def _format_timestamp(self, timestamp: float) -> str:
        """格式化时间戳"""
        msg_time = datetime.fromtimestamp(timestamp)
        now = datetime.now()

        if msg_time.date() == now.date():
            return msg_time.strftime("%H:%M")
        elif (now.date() - msg_time.date()).days == 1:
            return "昨天 " + msg_time.strftime("%H:%M")
        else:
            return msg_time.strftime("%m-%d %H:%M")

    def add_message_to_display(self, content: str, sender: str, time: str, is_self: bool = False):
        """添加消息到显示区域（旧版本，兼容性）"""
        # 根据发送者设置不同的格式
        if is_self:
            prefix = f"[{time}] 我: "
            color = "blue"
        else:
            prefix = f"[{time}] {sender}: "
            color = "green"

        # 添加带格式的消息
        self.messages_display.append(f'<font color="{color}"><b>{prefix}</b>{content}</font>')
        # 滚动到底部
        self.messages_display.verticalScrollBar().setValue(
            self.messages_display.verticalScrollBar().maximum()
        )

    def add_message(self, message: Message, sender_name: Optional[str] = None, is_self: Optional[bool] = None):
        """添加消息对象到显示区域"""
        if is_self is None:
            is_self = message.sender_id == self.chat_service.get_current_user_id()

        if sender_name is None:
            if is_self:
                sender_name = "我"
            else:
                sender_name = self.chat_service.get_contact_display_name(message.sender_id)

        # 格式化时间
        time_str = self._format_timestamp(message.timestamp)

        # 状态指示
        status_indicator = ""
        if message.status == MessageStatus.SENDING:
            status_indicator = " ⌛"
        elif message.status == MessageStatus.SENT:
            status_indicator = " ✓"
        elif message.status == MessageStatus.DELIVERED:
            status_indicator = " ✓✓"
        elif message.status == MessageStatus.READ:
            status_indicator = " ✓✓✓"
        elif message.status == MessageStatus.FAILED:
            status_indicator = " ✗"

        # 根据发送者设置颜色
        if is_self:
            color = "blue"
            prefix = f"[{time_str}] 我: "
        else:
            color = "green"
            prefix = f"[{time_str}] {sender_name}: "

        # 检查是否为解密失败的消息
        content = message.content
        is_decryption_failed = content.startswith("[Encrypted message:")
        is_file_event = (
            isinstance(message.metadata, dict)
            and message.metadata.get("schema") == "file_event_v1"
        )
        is_temp_file = (
            isinstance(message.metadata, dict)
            and message.metadata.get("schema") == TEMP_FILE_SCHEMA
        )
        
        # 添加带格式的消息
        if is_temp_file:
            self.messages_display.append(self._render_temp_file_message(prefix, message, status_indicator))
        elif is_file_event:
            self.messages_display.append(
                f'<font color="darkCyan"><b>{prefix}</b>{content}{status_indicator}</font>'
            )
        elif is_decryption_failed:
            # 解密失败的消息使用灰色斜体，添加警告图标
            self.messages_display.append(
                f'<font color="gray"><i><b>{prefix}</b>{content} ⚠️{status_indicator}</i></font>'
            )
        else:
            self.messages_display.append(
                f'<font color="{color}"><b>{prefix}</b>{content}{status_indicator}</font>'
            )
        # 滚动到底部
        self.messages_display.verticalScrollBar().setValue(
            self.messages_display.verticalScrollBar().maximum()
        )

    def _render_temp_file_message(self, prefix: str, message: Message, status_indicator: str) -> str:
        metadata = message.metadata if isinstance(message.metadata, dict) else {}
        file_name = html.escape(str(metadata.get("file_name") or "临时文件"))
        size = self._format_file_size(int(metadata.get("size") or 0))
        expires_at = float(metadata.get("expires_at") or 0)
        remaining = int(expires_at - time.time())
        if remaining <= 0:
            action = '<span style="color: gray;">已过期</span>'
            remain_text = "已过期"
        else:
            minutes = remaining // 60
            seconds = remaining % 60
            remain_text = f"剩余 {minutes:02d}:{seconds:02d}"
            action = f'<a href="imt-temp-file://{html.escape(message.id)}">下载</a>'
        return (
            f'<font color="darkMagenta"><b>{html.escape(prefix)}</b>'
            f'[临时文件] {file_name} ({size}) {html.escape(remain_text)} '
            f'{action}{html.escape(status_indicator)}</font>'
        )

    def send_message(self):
        """发送消息"""
        if self.active_target and self.active_target.get("kind") == "group":
            group_id = self.active_target.get("id", "")
            content = self.message_input.text().strip()
            if not content:
                return
            message = self.chat_service.send_group_message(content, group_id)
            if message:
                self.message_input.clear()
            else:
                QMessageBox.warning(self, "发送失败", "群消息发送失败，请检查连接或成员状态")
            return

        if not self.active_contact_id:
            QMessageBox.warning(self, "警告", "请先选择联系人")
            return

        block_reason = self.chat_service.get_send_block_reason(self.active_contact_id)
        if block_reason is not None:
            QMessageBox.warning(self, "发送已拦截", block_reason)
            self._refresh_send_controls()
            return

        content = self.message_input.text().strip()
        if not content:
            return

        # 通过聊天服务发送消息
        message = self.chat_service.send_message(content, self.active_contact_id)

        if message:
            # 消息已由事件系统添加到显示区域
            # 只需清空输入框
            self.message_input.clear()
        else:
            QMessageBox.warning(self, "发送失败", "消息发送失败，请检查连接")

    def send_temp_file(self):
        """选择并发送 30 分钟有效的临时加密文件。"""
        if not self.active_target and not self.active_contact_id:
            QMessageBox.warning(self, "警告", "请先选择联系人或群聊")
            return
        file_path, _ = QFileDialog.getOpenFileName(self, "选择临时发送文件")
        if not file_path:
            return
        if not self._confirm_temp_file_send(file_path):
            return
        try:
            if self.active_target and self.active_target.get("kind") == "group":
                message = self.chat_service.send_temp_file(file_path, group_id=self.active_target.get("id", ""))
            else:
                message = self.chat_service.send_temp_file(file_path, receiver_id=self.active_contact_id)
            if message is None:
                QMessageBox.warning(self, "发送失败", "临时文件发送失败，请检查加密链路、Hub 或文件大小")
        except TempFileServiceError as exc:
            QMessageBox.warning(self, "发送失败", self._temp_file_error_text(exc))
        except Exception as exc:
            QMessageBox.warning(self, "发送失败", str(exc))

    def on_message_link_clicked(self, url: QUrl):
        if url.scheme() != "imt-temp-file":
            return
        message_id = url.host() or url.path().lstrip("/")
        if not message_id:
            return
        try:
            output_dir = QFileDialog.getExistingDirectory(self, "选择保存目录")
            if not output_dir:
                return
            path = self.chat_service.download_temp_file(message_id, output_dir=output_dir)
            QMessageBox.information(self, "下载完成", f"文件已保存到:\n{path}")
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(path).parent)))
        except TempFileServiceError as exc:
            QMessageBox.warning(self, "下载失败", self._temp_file_error_text(exc))
        except Exception as exc:
            QMessageBox.warning(self, "下载失败", str(exc))

    def _confirm_temp_file_send(self, file_path: str) -> bool:
        file_name = Path(file_path).name
        message = (
            f"准备发送临时文件：{file_name}\n\n"
            "文件会在本机使用 AES-GCM 加密后上传到 Hub。\n"
            "Hub 只保存密文，不保存明文。\n"
            "默认 30 分钟后过期并删除。"
        )
        return QMessageBox.question(
            self,
            "发送临时加密文件",
            message,
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Ok,
            QMessageBox.StandardButton.Ok,
        ) == QMessageBox.StandardButton.Ok

    def _temp_file_error_text(self, exc: TempFileServiceError) -> str:
        labels = {
            "hub_offline": "Hub 不在线",
            "expired": "文件已过期",
            "decrypt_failed": "解密失败",
            "network_error": "网络错误",
        }
        label = labels.get(getattr(exc, "reason", ""), "临时文件失败")
        return f"{label}\n\n{exc}"

    def add_group_members(self):
        """向当前群聊发送成员邀请。"""
        if not self.active_target or self.active_target.get("kind") != "group":
            return
        group_id = self.active_target.get("id", "")
        member_text, ok = QInputDialog.getText(
            self,
            "添加群成员",
            "请输入已授权联系人 user_id，多个用空格分隔:",
            QLineEdit.EchoMode.Normal,
            "",
        )
        if not ok:
            return
        member_ids = [item.strip() for item in member_text.split() if item.strip()]
        if not member_ids:
            return

        failed = []
        for member_id in member_ids:
            if not self.chat_service.add_group_member(group_id, member_id):
                failed.append(member_id)

        self.set_active_group(group_id)
        if failed:
            QMessageBox.warning(self, "部分邀请失败", f"以下 user_id 未能邀请: {' '.join(failed)}")
        else:
            QMessageBox.information(self, "邀请已发送", "对方接受邀请后才会加入群聊。")

    def _current_group_id(self) -> str:
        if self.active_target and self.active_target.get("kind") == "group":
            return self.active_target.get("id", "")
        return ""

    def _set_sync_controls_visible(self, visible: bool) -> None:
        self.sync_status_label.setVisible(visible)
        self.sync_actions_widget.setVisible(visible)

    def _render_sync_overview(self, overview: Dict[str, Any]) -> None:
        project = overview.get("project") or {}
        folder = overview.get("shared_folder") or {}
        devices = overview.get("devices") or []
        status = overview.get("status") or "unconfigured"
        completion = float(overview.get("completion") or 0.0)
        error = overview.get("error") or ""

        if not folder:
            text = "文件同步: 未配置"
        else:
            project_name = project.get("name") or folder.get("name") or "项目文件夹"
            local_path = folder.get("local_path") or "未选择路径"
            syncthing_folder_id = folder.get("syncthing_folder_id") or "未配置"
            text = (
                f"文件同步: {self._display_sync_status(status)} | "
                f"项目: {project_name} | "
                f"目录: {local_path} | "
                f"Folder: {syncthing_folder_id} | "
                f"完成度: {completion:.1f}% | "
                f"设备: {len(devices)}"
            )
            if error:
                text += f" | 错误: {error}"

        color = "gray"
        if status in {"connected", "configured", "local_bound", "syncing", "scanning"}:
            color = "orange"
        if status == "synced":
            color = "green"
        if status in {"error", "notsharing", "forbidden"}:
            color = "red"

        self.sync_status_label.setText(text)
        self.sync_status_label.setStyleSheet(f"color: {color};")
        self.configure_sync_button.setEnabled(bool(folder))
        self.add_sync_device_button.setEnabled(bool(folder))
        self.refresh_sync_button.setEnabled(bool(folder and folder.get("syncthing_folder_id")))
        self.scan_sync_button.setEnabled(bool(folder and folder.get("syncthing_folder_id")))
        self.search_project_files_button.setEnabled(bool(folder))
        self.stop_sync_button.setEnabled(bool(folder and folder.get("syncthing_folder_id")))

    def _display_sync_status(self, status: str) -> str:
        mapping = {
            "unconfigured": "未配置",
            "reserved": "未配置",
            "local_bound": "已绑定本地目录",
            "configured": "已配置",
            "scanning": "扫描中",
            "syncing": "同步中",
            "synced": "已同步",
            "idle": "空闲",
            "paused": "已暂停",
            "notsharing": "设备未共享",
            "stopped": "已停止",
            "error": "错误",
            "forbidden": "无权限",
        }
        return mapping.get(status, status or "未知")

    def update_sync_overview(self, group_id: str, poll: bool = False, publish_file_events: bool = False) -> None:
        try:
            if poll:
                overview = self.chat_service.refresh_group_sync_status(group_id, publish_file_events=publish_file_events)
            else:
                overview = self.chat_service.get_group_sync_overview(group_id)
            self._render_sync_overview(overview)
        except Exception as e:
            self.logger.error(f"刷新同步状态失败: {e}", exc_info=True)
            self.sync_status_label.setText(f"文件同步: 错误 ({e})")
            self.sync_status_label.setStyleSheet("color: red;")

    def bind_group_folder(self):
        """为当前群聊选择本地项目文件夹。"""
        group_id = self._current_group_id()
        if not group_id:
            return
        directory = QFileDialog.getExistingDirectory(self, "选择项目文件夹")
        if not directory:
            return
        overview = self.chat_service.bind_group_folder(group_id, directory)
        if overview is None:
            QMessageBox.warning(self, "绑定失败", "请选择有效目录，并确认当前用户仍是群成员。")
            return
        self._render_sync_overview(overview)
        QMessageBox.information(self, "已绑定", "该群组已绑定本地项目文件夹。")

    def edit_syncthing_settings(self):
        """配置当前 profile 的 Syncthing REST API 地址和 API key。"""
        settings = self.chat_service.get_syncthing_settings()
        base_url, ok = QInputDialog.getText(
            self,
            "Syncthing API 地址",
            "请输入 Syncthing API 地址:",
            QLineEdit.EchoMode.Normal,
            settings.get("base_url") or "http://127.0.0.1:8384",
        )
        if not ok:
            return
        api_key, ok = QInputDialog.getText(
            self,
            "Syncthing API Key",
            "请输入 API key（留空则保持已有 key 不变）:",
            QLineEdit.EchoMode.Password,
            "",
        )
        if not ok:
            return

        self.chat_service.save_syncthing_settings(
            base_url=base_url.strip(),
            api_key=api_key.strip() if api_key.strip() else None,
        )
        detection = self.chat_service.detect_local_syncthing()
        state = detection.get("state", "unknown")
        self.status_bar_message(f"Syncthing 状态: {state}")
        group_id = self._current_group_id()
        if group_id:
            self.update_sync_overview(group_id)

    def configure_syncthing_folder(self):
        """把当前群组项目文件夹写入 Syncthing folder/device 配置。"""
        group_id = self._current_group_id()
        if not group_id:
            return
        overview = self.chat_service.configure_group_syncthing_folder(group_id)
        if overview is None:
            QMessageBox.warning(self, "配置失败", "请先绑定目录并确认 Syncthing API 设置正确。")
            return
        self._render_sync_overview(overview)
        if overview.get("restart_required"):
            QMessageBox.information(self, "需要重启 Syncthing", "Syncthing 配置已更新，但需要重启后完全生效。")
        else:
            QMessageBox.information(self, "配置完成", "Syncthing folder/device 配置已更新。")

    def add_sync_device(self):
        """手动添加群成员 Syncthing Device ID。"""
        group_id = self._current_group_id()
        if not group_id:
            return
        raw_text, ok = QInputDialog.getText(
            self,
            "添加同步设备",
            "请输入: 群成员 user_id SyncthingDeviceID",
            QLineEdit.EchoMode.Normal,
            "",
        )
        if not ok:
            return
        parts = raw_text.split()
        if len(parts) < 2:
            QMessageBox.warning(self, "格式错误", "请按“user_id SyncthingDeviceID”格式输入。")
            return
        user_id = parts[0]
        device_id = "".join(parts[1:])
        result = self.chat_service.add_group_sync_device(group_id, user_id, device_id)
        if result is None:
            QMessageBox.warning(self, "添加失败", "请确认 user_id 是群成员，Device ID 不为空。")
            return
        self.update_sync_overview(group_id)
        QMessageBox.information(self, "已添加", "同步设备已记录。再次点击“配置Syncthing”可更新 folder 共享设备。")

    def refresh_sync_status(self):
        """刷新当前群聊同步状态并发布新的文件事件 metadata。"""
        group_id = self._current_group_id()
        if not group_id:
            return
        self.update_sync_overview(group_id, poll=True, publish_file_events=True)

    def scan_sync_folder(self):
        """请求 Syncthing 扫描当前群聊项目文件夹。"""
        group_id = self._current_group_id()
        if not group_id:
            return
        overview = self.chat_service.scan_group_sync_folder(group_id)
        if overview is None:
            QMessageBox.warning(self, "扫描失败", "请确认 Syncthing 已连接且该群组已配置 folder。")
            return
        self._render_sync_overview(overview)

    def search_project_files(self):
        """Search indexed files for the active group project."""
        group_id = self._current_group_id()
        if not group_id:
            return

        query, ok = QInputDialog.getText(
            self,
            "搜索项目文件",
            "请输入文件名、扩展名或相对路径:",
            QLineEdit.EchoMode.Normal,
            "",
        )
        if not ok:
            return

        try:
            if not self.chat_service.get_project_index_status(group_id).get("existing_count"):
                self.chat_service.scan_project_index(group_id)
            results = self.chat_service.search_project_files(query=query.strip(), group_id=group_id, limit=50)
        except Exception as e:
            self.logger.error(f"搜索项目文件失败: {e}", exc_info=True)
            QMessageBox.warning(self, "搜索失败", str(e))
            return

        if not results:
            QMessageBox.information(self, "没有结果", "没有找到匹配的项目文件。")
            return
        self.show_project_file_results(results)

    def show_project_file_results(self, results: List[Dict[str, Any]]) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("项目文件搜索")
        dialog.resize(760, 420)
        layout = QVBoxLayout()

        list_widget = QListWidget()
        for result in results:
            size = int(result.get("size") or 0)
            display = (
                f"{result.get('file_name', '')}  "
                f"{self._format_file_size(size)}\n"
                f"{result.get('relative_path', '')}"
            )
            item = QListWidgetItem(display)
            item.setData(Qt.ItemDataRole.UserRole, result)
            item.setToolTip(result.get("absolute_path", ""))
            list_widget.addItem(item)
        layout.addWidget(list_widget)

        actions = QHBoxLayout()
        open_button = QPushButton("打开")
        reveal_button = QPushButton("在 Finder 中显示")
        close_button = QPushButton("关闭")
        actions.addWidget(open_button)
        actions.addWidget(reveal_button)
        actions.addStretch(1)
        actions.addWidget(close_button)
        layout.addLayout(actions)
        dialog.setLayout(layout)

        def selected_result() -> Optional[Dict[str, Any]]:
            item = list_widget.currentItem()
            return item.data(Qt.ItemDataRole.UserRole) if item else None

        def open_selected() -> None:
            result = selected_result()
            path = (result or {}).get("absolute_path", "")
            if path:
                QDesktopServices.openUrl(QUrl.fromLocalFile(path))

        def reveal_selected() -> None:
            result = selected_result()
            path = (result or {}).get("absolute_path", "")
            if not path:
                return
            if sys.platform == "darwin":
                subprocess.run(["open", "-R", path], check=False)
            else:
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(path).parent)))

        open_button.clicked.connect(open_selected)
        reveal_button.clicked.connect(reveal_selected)
        close_button.clicked.connect(dialog.accept)
        list_widget.itemDoubleClicked.connect(lambda _item: open_selected())
        dialog.exec()

    def _format_file_size(self, size: int) -> str:
        units = ["B", "KB", "MB", "GB"]
        value = float(size)
        for unit in units:
            if value < 1024 or unit == units[-1]:
                return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
            value /= 1024
        return f"{size} B"

    def stop_syncthing_sync(self):
        """删除本机 Syncthing folder 配置，保留项目绑定和本地文件。"""
        group_id = self._current_group_id()
        if not group_id:
            return

        reply = QMessageBox.question(
            self,
            "停止同步",
            "确定要停止本机 Syncthing 快速同步吗？\n\n"
            "此操作不会删除本地文件，不会解绑项目目录，也不会删除聊天记录。\n"
            "它只会从本机 Syncthing 配置中移除当前群组的 folder。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        overview = self.chat_service.stop_group_syncthing_folder(group_id)
        if overview is None:
            QMessageBox.warning(self, "停止失败", "请确认 Syncthing API 设置正确，且当前用户仍是群成员。")
            return
        self._render_sync_overview(overview)
        if overview.get("restart_required"):
            QMessageBox.information(self, "需要重启 Syncthing", "同步已停止，但需要手动重启 Syncthing 后完全生效。")
        else:
            QMessageBox.information(self, "已停止", "本机 Syncthing 快速同步已停止，本地项目文件和绑定信息已保留。")

    def status_bar_message(self, message: str) -> None:
        window = self.window()
        status_bar = getattr(window, "status_bar", None)
        if status_bar is not None:
            status_bar.showMessage(message, 4000)

    def _start_temp_file_refresh_timer(self) -> None:
        if self.temp_file_refresh_timer is not None:
            return
        self.temp_file_refresh_timer = QTimer(self)
        self.temp_file_refresh_timer.setInterval(30000)
        self.temp_file_refresh_timer.timeout.connect(self.refresh_temp_file_countdowns)
        self.temp_file_refresh_timer.start()

    def refresh_temp_file_countdowns(self) -> None:
        if not self.active_target and not self.active_contact_id:
            return
        try:
            if self.active_target and self.active_target.get("kind") == "group":
                group_id = self.active_target.get("id", "")
                messages = self.chat_service.load_messages_for_conversation(group_id, limit=50)
                if any(self._message_is_temp_file(message) for message in messages):
                    self.set_active_group(group_id)
            elif self.active_contact_id:
                messages = self.chat_service.load_messages_for_contact(self.active_contact_id, limit=50)
                if any(self._message_is_temp_file(message) for message in messages):
                    self.set_active_contact(
                        self.active_contact_id,
                        self.chat_service.get_contact_display_name(self.active_contact_id),
                    )
        except Exception as exc:
            self.logger.debug(f"刷新临时文件倒计时失败: {exc}")

    def _message_is_temp_file(self, message: Message) -> bool:
        return isinstance(message.metadata, dict) and message.metadata.get("schema") == TEMP_FILE_SCHEMA

    def set_active_contact(self, contact_id: str, contact_name: str):
        """设置当前聊天联系人"""
        self.active_contact_id = contact_id
        self.active_target = {
            "kind": "direct",
            "id": contact_id,
            "conversation_id": None,
        }
        self.title_label.setText(f"与 {contact_name} 的对话")
        self.members_label.hide()
        self._set_sync_controls_visible(False)
        self.add_group_member_button.hide()
        self._refresh_send_controls()

        # 清空消息显示区域
        self.messages_display.clear()

        # 加载历史消息
        try:
            messages = self.chat_service.load_messages_for_contact(contact_id, limit=50)
            if messages:
                # 过滤只显示已授权的消息
                trusted_messages = [msg for msg in messages if msg.auth_status == MessageAuthStatus.TRUSTED]
                pending_count = len([msg for msg in messages if msg.auth_status == MessageAuthStatus.PENDING])
                rejected_count = len([msg for msg in messages if msg.auth_status == MessageAuthStatus.REJECTED])
                
                for message in trusted_messages:
                    self.add_message(message)
                
                self.logger.info(
                    f"加载 {len(trusted_messages)} 条已授权消息，{pending_count} 条待授权消息，"
                    f"{rejected_count} 条已拒绝消息"
                )
                
                # 如果有待授权消息，显示提示
                if pending_count > 0:
                    self.messages_display.append(
                        f'<font color="orange"><i>有 {pending_count} 条消息仍在 pending，接受后才会进入正式聊天。</i></font>'
                    )
                if rejected_count > 0:
                    self.messages_display.append(
                        f'<font color="red"><i>有 {rejected_count} 条消息已被拒绝，不会进入正式聊天。</i></font>'
                    )
            else:
                self.messages_display.append('<font color="gray"><i>开始新的对话...</i></font>')
        except Exception as e:
            self.logger.error(f"加载历史消息失败: {e}")
            self.messages_display.append('<font color="gray"><i>开始新的对话...</i></font>')

    def set_active_group(self, group_id: str):
        """设置当前群聊。"""
        self.active_contact_id = None
        self.active_target = {
            "kind": "group",
            "id": group_id,
            "conversation_id": group_id,
        }
        group_name = self.chat_service.get_group_display_name(group_id)
        members = self.chat_service.get_group_members(group_id)
        member_names = [
            member.display_name or member.user_id
            for member in members
        ]

        self.title_label.setText(f"{group_name}")
        self.members_label.setText(f"成员: {', '.join(member_names) if member_names else '无'}")
        self.members_label.show()
        self._set_sync_controls_visible(True)
        self.update_sync_overview(group_id)
        self.add_group_member_button.show()
        self._refresh_send_controls()

        self.messages_display.clear()
        try:
            messages = self.chat_service.load_messages_for_conversation(group_id, limit=50)
            if messages:
                for message in messages:
                    self.add_message(message)
            else:
                self.messages_display.append('<font color="gray"><i>开始新的群聊...</i></font>')
        except Exception as e:
            self.logger.error(f"加载群消息失败: {e}")
            self.messages_display.append('<font color="gray"><i>开始新的群聊...</i></font>')

    def is_message_for_active_contact(self, message: Message) -> bool:
        """判断消息是否属于当前打开的联系人聊天"""
        if self.active_target and self.active_target.get("kind") == "group":
            return self.chat_service.is_message_for_conversation(
                message,
                self.active_target.get("conversation_id") or self.active_target.get("id", ""),
            )
        if not self.active_contact_id:
            return False

        return self.chat_service.is_message_for_contact(message, self.active_contact_id)


class AIAssistantPanel(QFrame):
    """聊天窗口旁边的本地 AI 助手侧栏。"""

    task_finished = Signal(str, object, object)

    def __init__(self, chat_service: ChatService, parent=None):
        super().__init__(parent)
        self.chat_service = chat_service
        self.logger = get_logger("ai_assistant_panel")
        self.active_target: Dict[str, Any] = {}
        self.ai_conversation_id = ""
        self._task_running = False
        self.setFrameStyle(QFrame.Shape.StyledPanel | QFrame.Shadow.Raised)
        self.setMinimumWidth(280)
        self.setMaximumWidth(420)
        self.task_finished.connect(self._on_task_finished, Qt.ConnectionType.QueuedConnection)
        self.init_ui()
        self.set_active_target({})

    def init_ui(self):
        layout = QVBoxLayout()

        title = QLabel("AI 助手")
        title.setFont(QFont("Arial", 14, QFont.Weight.Bold))
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        self.context_label = QLabel("")
        self.context_label.setWordWrap(True)
        layout.addWidget(self.context_label)

        self.privacy_label = QLabel("")
        self.privacy_label.setWordWrap(True)
        self.privacy_label.setStyleSheet("color: gray;")
        layout.addWidget(self.privacy_label)

        self.provider_label = QLabel("Provider: 未配置")
        self.provider_label.setWordWrap(True)
        layout.addWidget(self.provider_label)

        self.library_label = QLabel("")
        self.library_label.setWordWrap(True)
        layout.addWidget(self.library_label)

        action_row = QHBoxLayout()
        self.refresh_button = QPushButton("刷新")
        self.refresh_button.clicked.connect(self.refresh_context)
        action_row.addWidget(self.refresh_button)
        self.build_button = QPushButton("构建")
        self.build_button.clicked.connect(self.build_library)
        action_row.addWidget(self.build_button)
        self.diagnose_button = QPushButton("诊断")
        self.diagnose_button.clicked.connect(self.diagnose_library)
        action_row.addWidget(self.diagnose_button)
        layout.addLayout(action_row)

        self.source_search_input = QLineEdit()
        self.source_search_input.setPlaceholderText("搜索本机 AI 文档库记录")
        self.source_search_input.returnPressed.connect(self.search_sources)
        layout.addWidget(self.source_search_input)

        source_action_row = QHBoxLayout()
        self.search_sources_button = QPushButton("检索记录")
        self.search_sources_button.clicked.connect(self.search_sources)
        source_action_row.addWidget(self.search_sources_button)
        self.delete_source_button = QPushButton("删除索引")
        self.delete_source_button.clicked.connect(self.delete_selected_source)
        source_action_row.addWidget(self.delete_source_button)
        self.restore_source_button = QPushButton("恢复")
        self.restore_source_button.clicked.connect(self.restore_selected_source)
        source_action_row.addWidget(self.restore_source_button)
        layout.addLayout(source_action_row)

        self.sources_list = QListWidget()
        self.sources_list.setMinimumHeight(110)
        self.sources_list.setMaximumHeight(180)
        layout.addWidget(self.sources_list)

        self.chat_display = QTextBrowser()
        self.chat_display.setReadOnly(True)
        self.chat_display.setMinimumHeight(180)
        layout.addWidget(self.chat_display, 1)

        self.answer_sources_label = QLabel("")
        self.answer_sources_label.setWordWrap(True)
        self.answer_sources_label.setStyleSheet("color: gray;")
        layout.addWidget(self.answer_sources_label)

        ask_row = QHBoxLayout()
        self.question_input = QLineEdit()
        self.question_input.setPlaceholderText("问 AI...")
        self.question_input.returnPressed.connect(self.ask_question)
        ask_row.addWidget(self.question_input)
        self.ask_button = QPushButton("提问")
        self.ask_button.clicked.connect(self.ask_question)
        ask_row.addWidget(self.ask_button)
        layout.addLayout(ask_row)

        self.setLayout(layout)

    def set_active_target(self, target: Dict[str, Any]):
        self.active_target = dict(target or {})
        self.ai_conversation_id = ""
        self.chat_display.clear()
        self.answer_sources_label.setText("")
        self._render_context()
        self.refresh_context()

    def refresh_context(self):
        provider = self.chat_service.get_ai_provider_status()
        self.provider_label.setText(
            f"Provider: {provider.get('provider_type') or '未选择'} / "
            f"{provider.get('provider_location')} / {provider.get('model') or '未配置'}"
        )
        if self._current_kind() != "group":
            self.library_label.setText("单聊模式: 只发送最近聊天上下文，不使用项目文档库。")
            self.sources_list.clear()
            self._update_controls()
            return
        group_id = self._current_group_id()
        self._run_task("status", lambda: self.chat_service.get_ai_document_library_status(group_id))
        self._run_task("sources", lambda: self.chat_service.list_ai_document_sources(
            group_id=group_id,
            query=self.source_search_input.text().strip(),
            limit=30,
        ))

    def build_library(self):
        group_id = self._current_group_id()
        if not group_id:
            return
        self._run_task("build", lambda: self.chat_service.build_ai_document_library(group_id))

    def diagnose_library(self):
        group_id = self._current_group_id()
        if not group_id:
            return
        query = self.question_input.text().strip() or self.source_search_input.text().strip()
        self._run_task("diagnose", lambda: self.chat_service.diagnose_ai_document_library(group_id, query=query))

    def search_sources(self):
        group_id = self._current_group_id()
        if not group_id:
            return
        query = self.source_search_input.text().strip()
        self._run_task("sources", lambda: self.chat_service.list_ai_document_sources(
            group_id=group_id,
            query=query,
            limit=60,
        ))

    def delete_selected_source(self):
        source = self._selected_source()
        group_id = self._current_group_id()
        if not source or not group_id:
            return
        source_id = source.get("source_id") or ""
        if not source_id:
            return
        confirmed = QMessageBox.question(
            self,
            "删除本机 AI 索引",
            "只删除当前 profile 的 AI 文档库索引记录，不会删除真实文件，也不会影响其他成员。继续？",
        )
        if confirmed != QMessageBox.StandardButton.Yes:
            return
        self._run_task("delete_source", lambda: self.chat_service.delete_ai_document_source(
            source_id=source_id,
            group_id=group_id,
        ))

    def restore_selected_source(self):
        source = self._selected_source()
        group_id = self._current_group_id()
        if not source or not group_id:
            return
        source_id = source.get("source_id") or ""
        if not source_id:
            return
        self._run_task("restore_source", lambda: self.chat_service.restore_ai_document_source(
            source_id=source_id,
            group_id=group_id,
        ))

    def ask_question(self):
        question = self.question_input.text().strip()
        if not question:
            return
        target = dict(self.active_target or {})
        kind = target.get("kind", "")
        target_id = target.get("id", "")
        self.question_input.clear()
        self._append_ai_message("我", question, "blue")
        self._run_task("ask", lambda: self.chat_service.ask_ai_assistant(
            question=question,
            target_kind=kind,
            target_id=target_id,
            conversation_id=self.ai_conversation_id,
        ))

    def _run_task(self, name: str, func):
        if self._task_running and name in {"ask", "build", "diagnose", "delete_source", "restore_source"}:
            return

        def worker():
            try:
                result = func()
                self.task_finished.emit(name, result, None)
            except Exception as exc:
                self.task_finished.emit(name, None, str(exc))

        self._task_running = True
        self._update_controls()
        threading.Thread(target=worker, name=f"ai_assistant_{name}", daemon=True).start()

    @Slot(str, object, object)
    def _on_task_finished(self, name: str, result: object, error: object):
        self._task_running = False
        if error:
            self._append_ai_message("系统", str(error), "red")
            self._update_controls()
            return
        data = result if isinstance(result, dict) else {}
        if name == "status":
            self._render_status(data)
        elif name == "build":
            library = data.get("library") if isinstance(data.get("library"), dict) else {}
            self._render_status(library)
            summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}
            self._append_ai_message("系统", f"文档库已更新，chunk={summary.get('chunk_count', 0)}", "gray")
            self.search_sources()
        elif name == "diagnose":
            checks = data.get("checks") or []
            lines = [f"{item.get('key')}: {item.get('status')} - {item.get('message')}" for item in checks[:8]]
            rag_prompt = data.get("rag_prompt") or {}
            lines.append(f"prompt_has_sources: {rag_prompt.get('prompt_contains_sources')}")
            self._append_ai_message("诊断", "\n".join(lines), "darkCyan")
        elif name == "sources":
            self._render_source_records(data.get("sources") or [])
        elif name in {"delete_source", "restore_source"}:
            self._append_ai_message("系统", "本机 AI 文档库记录已更新，真实文件未删除。", "gray")
            self.refresh_context()
        elif name == "ask":
            self.ai_conversation_id = str(data.get("conversation_id") or "")
            answer = str(data.get("answer") or "")
            self._append_ai_message("AI", answer, "green")
            self._render_answer_sources(data.get("sources") or [])
        self._update_controls()

    def _render_context(self):
        kind = self._current_kind()
        if kind == "group":
            self.context_label.setText(f"当前群聊: {self._current_group_id()}")
            self.privacy_label.setText("将发送: 当前问题、最近聊天上下文、检索命中的本地文档片段。")
        elif kind == "direct":
            self.context_label.setText(f"当前单聊: {self.active_target.get('id', '')}")
            self.privacy_label.setText("将发送: 当前问题和最近单聊上下文；不会读取项目文档库。")
        else:
            self.context_label.setText("请选择聊天对象")
            self.privacy_label.setText("AI 不执行命令、不修改文件、不联网搜索。")

    def _render_status(self, status: Dict[str, Any]):
        self.library_label.setText(
            "文档库: "
            f"{status.get('indexed_source_count', 0)}/{status.get('candidate_count', 0)} 文件, "
            f"{status.get('chunk_count', 0)} chunks, "
            f"待处理 {status.get('pending_count', 0)}, "
            f"本机删除 {status.get('deleted_local_count', 0)}"
        )

    def _render_source_records(self, sources: List[Dict[str, Any]]):
        self.sources_list.clear()
        for source in sources:
            status = source.get("content_status") or ""
            display = (
                f"[{status}] {source.get('file_name', '')}  "
                f"chunks={source.get('chunk_count', 0)}\n"
                f"{source.get('relative_path', '')}"
            )
            item = QListWidgetItem(display)
            item.setData(Qt.ItemDataRole.UserRole, source)
            item.setToolTip(source.get("absolute_path", ""))
            self.sources_list.addItem(item)

    def _render_answer_sources(self, sources: List[Dict[str, Any]]):
        if not sources:
            self.answer_sources_label.setText("来源: 无")
            return
        labels = [
            f"[{source.get('source_index', '?')}] {source.get('relative_path', '')}:"
            f"{source.get('line_start', 1)}-{source.get('line_end', 1)}"
            for source in sources[:5]
        ]
        self.answer_sources_label.setText("来源: " + " | ".join(labels))

    def _append_ai_message(self, sender: str, content: str, color: str):
        safe_sender = html.escape(sender)
        safe_content = html.escape(content).replace("\n", "<br>")
        self.chat_display.append(f'<font color="{color}"><b>{safe_sender}: </b>{safe_content}</font>')
        self.chat_display.verticalScrollBar().setValue(self.chat_display.verticalScrollBar().maximum())

    def _selected_source(self) -> Optional[Dict[str, Any]]:
        item = self.sources_list.currentItem()
        if not item:
            return None
        data = item.data(Qt.ItemDataRole.UserRole)
        return data if isinstance(data, dict) else None

    def _current_kind(self) -> str:
        return str((self.active_target or {}).get("kind") or "")

    def _current_group_id(self) -> str:
        if self._current_kind() == "group":
            return str(self.active_target.get("id") or "")
        return ""

    def _update_controls(self):
        has_target = bool(self.active_target.get("id"))
        is_group = self._current_kind() == "group"
        self.ask_button.setEnabled(has_target and not self._task_running)
        self.question_input.setEnabled(has_target and not self._task_running)
        self.refresh_button.setEnabled(has_target and not self._task_running)
        self.build_button.setEnabled(is_group and not self._task_running)
        self.diagnose_button.setEnabled(is_group and not self._task_running)
        self.search_sources_button.setEnabled(is_group and not self._task_running)
        self.delete_source_button.setEnabled(is_group and not self._task_running)
        self.restore_source_button.setEnabled(is_group and not self._task_running)


class MainWindow(QMainWindow):
    """主窗口，整合所有面板"""

    # 跨线程信号
    ui_event_signal = Signal(object)
    contact_auth_required_signal = Signal(dict)
    pairing_request_signal = Signal(dict)
    group_invite_received_signal = Signal(dict)

    def __init__(
        self,
        current_user_id: str = "self",
        transport: Optional[Transport] = None,
        data_dir: Optional[str] = None,
        db_path: Optional[str] = None,
        config_dir: Optional[str] = None,
        display_name: Optional[str] = None,
        chat_service: Optional[ChatService] = None,
        identity_manager=None,
        store=None,
        key_store=None,
        pairing_manager=None,
    ):
        super().__init__()
        self.current_user_id = current_user_id
        self.active_contact_id = None
        self.active_target = None
        self.data_dir = data_dir
        self.db_path = db_path or "data/chat.db"
        self.config_dir = config_dir or "data/config"
        self.logger = get_logger("main_window")
        self._presence_refresh_running = False
        self.presence_refresh_timer: Optional[QTimer] = None

        self.setWindowTitle("即时聊天系统")
        self.setGeometry(100, 100, 1200, 700)

        # 连接跨线程信号到槽
        self.ui_event_signal.connect(self._dispatch_ui_event, Qt.ConnectionType.QueuedConnection)
        self.contact_auth_required_signal.connect(self._show_contact_auth_dialog, Qt.ConnectionType.QueuedConnection)
        self.pairing_request_signal.connect(self._show_pairing_dialog, Qt.ConnectionType.QueuedConnection)
        self.group_invite_received_signal.connect(self._show_group_invite_dialog, Qt.ConnectionType.QueuedConnection)

        # 初始化传输层和聊天服务
        if chat_service is not None:
            self.chat_service = chat_service
            self.transport = chat_service.transport
        elif transport is None:
            # 默认使用内存传输（向后兼容）
            self.transport = LocalMemoryTransport.get_global_instance("default")
            self.logger.info("使用默认内存传输")
            self.chat_service = ChatService(
                self.transport,
                current_user_id,
                db_path=self.db_path,
                display_name=display_name,
                store=store,
                identity_manager=identity_manager,
                key_store=key_store,
                pairing_manager=pairing_manager,
                config_dir=self.config_dir,
                data_dir=self.data_dir,
            )
        else:
            self.transport = transport
            self.logger.info(f"使用自定义传输: {transport.get_status().get('transport_type', 'unknown')}")
            self.chat_service = ChatService(
                self.transport,
                current_user_id,
                db_path=self.db_path,
                display_name=display_name,
                store=store,
                identity_manager=identity_manager,
                key_store=key_store,
                pairing_manager=pairing_manager,
                config_dir=self.config_dir,
                data_dir=self.data_dir,
            )

        self.current_user_id = self.chat_service.get_current_user_id()

        self.init_ui()
        self.connect_signals()
        self.subscribe_events()

        # 启动聊天服务
        if not self.chat_service.start():
            QMessageBox.critical(self, "错误", "聊天服务启动失败")
            self.close()
        else:
            self.contacts_panel.load_contacts()
            self._start_presence_refresh_timer()

        self.logger.info(f"主窗口初始化完成，用户: {self.current_user_id}")

    def _start_presence_refresh_timer(self) -> None:
        """启动轻量在线状态刷新兜底。"""
        if self.presence_refresh_timer is not None:
            return
        self.presence_refresh_timer = QTimer(self)
        self.presence_refresh_timer.setInterval(5000)
        self.presence_refresh_timer.timeout.connect(self._schedule_presence_refresh)
        self.presence_refresh_timer.start()
        QTimer.singleShot(1000, self._schedule_presence_refresh)

    def _schedule_presence_refresh(self) -> None:
        """在后台线程刷新在线状态，避免阻塞 UI。"""
        if self._presence_refresh_running:
            return
        self._presence_refresh_running = True
        worker = threading.Thread(
            target=self._refresh_presence_worker,
            name=f"presence_refresh_{self.current_user_id}",
            daemon=True,
        )
        worker.start()

    def _refresh_presence_worker(self) -> None:
        try:
            self.chat_service.refresh_presence()
        except Exception as e:
            self.logger.warning(f"刷新在线状态失败: {e}")
        finally:
            self._presence_refresh_running = False

    def init_ui(self):
        """初始化UI"""
        # 创建菜单栏
        menubar = self.menuBar()
        settings_menu = menubar.addMenu("设置")
        
        # 修改显示名称动作
        change_name_action = QAction("修改显示名称", self)
        change_name_action.triggered.connect(self.show_change_name_dialog)
        settings_menu.addAction(change_name_action)
        
        # 创建中央部件
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        # 主布局
        main_layout = QVBoxLayout(central_widget)

        # 当前身份区：把 display_name 修改入口放在主界面可见位置
        identity_bar = QFrame()
        identity_bar.setFrameStyle(QFrame.Shape.StyledPanel | QFrame.Shadow.Raised)
        identity_layout = QHBoxLayout(identity_bar)

        identity_title = QLabel("我的身份")
        identity_title.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        identity_layout.addWidget(identity_title)

        self.identity_summary_label = QLabel("身份: 加载中...")
        self.identity_summary_label.setObjectName("identitySummaryLabel")
        identity_layout.addWidget(self.identity_summary_label, 1)

        self.security_summary_label = QLabel("安全: 加载中...")
        self.security_summary_label.setObjectName("securitySummaryLabel")
        self.security_summary_label.setStyleSheet("color: #4b5563;")
        identity_layout.addWidget(self.security_summary_label, 1)

        self.ai_toggle_button = QPushButton("隐藏 AI")
        self.ai_toggle_button.clicked.connect(self.toggle_ai_panel)
        identity_layout.addWidget(self.ai_toggle_button)

        self.change_display_name_button = QPushButton("修改显示名称")
        self.change_display_name_button.setObjectName("changeDisplayNameButton")
        self.change_display_name_button.clicked.connect(self.show_change_name_dialog)
        identity_layout.addWidget(self.change_display_name_button)

        main_layout.addWidget(identity_bar)

        # 使用分割器实现可调整大小的面板
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # 创建两个面板（传递聊天服务）
        self.contacts_panel = ContactsPanel(self.chat_service)
        self.messages_panel = MessagesPanel(self.chat_service)
        self.ai_panel = AIAssistantPanel(self.chat_service)

        # 添加到分割器
        splitter.addWidget(self.contacts_panel)
        splitter.addWidget(self.messages_panel)
        splitter.addWidget(self.ai_panel)

        # 设置分割器初始大小比例
        splitter.setSizes([220, 720, 320])

        main_layout.addWidget(splitter, 1)
        
        # 初始化状态栏
        self.status_bar = self.statusBar()
        
        # 身份信息标签
        self.identity_label = QLabel("身份: 加载中...")
        self.status_bar.addPermanentWidget(self.identity_label)
        
        self.network_status_label = QLabel("网络状态: 未知")
        self.status_bar.addPermanentWidget(self.network_status_label)
        self.update_network_status("disconnected")
        
        # 更新身份显示
        self.update_identity_display()
    
    def update_identity_display(self):
        """更新身份显示"""
        try:
            current_user = self.chat_service.identity_manager.get_current_user()
            if current_user is None:
                raise ValueError("当前用户未设置")

            user_id = current_user.user_id
            raw_display_name = (current_user.display_name or "").strip()
            self.current_user_id = user_id
            display_name_text = raw_display_name or "未设置"
            identity_text = f"ID: {user_id} | 显示名称: {display_name_text}"
            self.identity_label.setText(identity_text)
            self.identity_summary_label.setText(identity_text)
            device = self.chat_service.get_current_device_summary()
            device_name = device.get("device_name") or "本机设备"
            fingerprint = device.get("device_fingerprint") or "-"
            crypto_mode = "direct v2 / group v1" if self.chat_service.direct_crypto_service else "legacy"
            self.security_summary_label.setText(
                f"设备: {device_name} | 指纹: {fingerprint} | 加密: {crypto_mode}"
            )
            self._update_window_title()
        except Exception as e:
            self.logger.error(f"更新身份显示失败: {e}")
            self.identity_label.setText("身份: 错误")
            self.identity_summary_label.setText("身份: 错误")
            self.security_summary_label.setText("安全: 错误")

    def toggle_ai_panel(self):
        """折叠/展开 AI 助手侧栏。"""
        visible = self.ai_panel.isVisible()
        self.ai_panel.setVisible(not visible)
        self.ai_toggle_button.setText("显示 AI" if visible else "隐藏 AI")

    def _update_window_title(self):
        """窗口标题与当前身份保持一致。"""
        current_user = self.chat_service.identity_manager.get_current_user()
        raw_display_name = (current_user.display_name or "").strip() if current_user else ""
        if raw_display_name:
            self.setWindowTitle(f"即时聊天系统 - {raw_display_name} ({self.current_user_id})")
        else:
            self.setWindowTitle(f"即时聊天系统 - {self.current_user_id}")
    
    def show_change_name_dialog(self):
        """显示修改显示名称对话框"""
        current_user = self.chat_service.identity_manager.get_current_user()
        if not current_user:
            QMessageBox.warning(self, "警告", "当前用户未设置")
            return
        
        # 创建输入对话框
        new_name, ok = QInputDialog.getText(
            self, 
            "修改显示名称",
            "请输入新的显示名称（可留空，仅显示 ID）:",
            QLineEdit.EchoMode.Normal,
            current_user.display_name
        )
        
        if ok:
            normalized_name = new_name.strip()
            success = self.change_display_name(normalized_name)
            if success:
                result_text = normalized_name or "未设置"
                QMessageBox.information(self, "成功", f"显示名称已更新为: {result_text}")
            else:
                QMessageBox.warning(self, "失败", "修改显示名称失败")

    def change_display_name(self, new_name: str) -> bool:
        """更新当前用户显示名，并同步刷新主界面与本地 identity。"""
        current_user = self.chat_service.identity_manager.get_current_user()
        if not current_user:
            self.logger.warning("修改显示名称失败：当前用户未设置")
            return False

        locked_user_id = current_user.user_id
        normalized_name = (new_name or "").strip()
        success = self.chat_service.identity_manager.update_display_name(normalized_name)
        if not success:
            return False

        refreshed_user = self.chat_service.identity_manager.get_current_user()
        if refreshed_user is None or refreshed_user.user_id != locked_user_id:
            self.logger.error("修改显示名称后 user_id 发生异常变化")
            return False

        self.current_user_id = locked_user_id
        self.chat_service.update_transport_display_name()
        self.update_identity_display()
        self.contacts_panel.load_contacts()
        self.status_bar.showMessage("显示名称已更新", 3000)
        return True
    
    def update_network_status(self, status: str):
        """更新网络状态显示
        
        Args:
            status: 状态字符串，可选值: "connected", "disconnected", "connecting"
        """
        status_texts = {
            "connected": "网络状态: 已连接",
            "disconnected": "网络状态: 断开连接",
            "connecting": "网络状态: 连接中..."
        }
        text = status_texts.get(status, f"网络状态: {status}")
        self.network_status_label.setText(text)
        
        # 设置颜色
        if status == "connected":
            self.network_status_label.setStyleSheet("color: green; font-weight: bold;")
        elif status == "disconnected":
            self.network_status_label.setStyleSheet("color: red; font-weight: bold;")
        elif status == "connecting":
            self.network_status_label.setStyleSheet("color: orange; font-weight: bold;")
        else:
            self.network_status_label.setStyleSheet("")

    def _set_messages_panel_for_contact(self, contact_id: str) -> None:
        """基于 active_contact_id 刷新消息面板"""
        if not contact_id:
            return

        contacts = self.chat_service.get_contacts()
        if contact_id not in contacts:
            self.logger.warning(f"尝试设置不存在的联系人: {contact_id}")
            return

        contact_name = self.chat_service.get_contact_display_name(contact_id)
        self.messages_panel.set_active_contact(contact_id, contact_name)
        self.active_contact_id = contact_id
        self.active_target = {
            "kind": "direct",
            "id": contact_id,
            "conversation_id": None,
        }
        if hasattr(self, "ai_panel"):
            self.ai_panel.set_active_target(self.active_target)
        self.logger.info(f"设置消息面板为联系人: {contact_name} ({contact_id})")

    def _set_messages_panel_for_group(self, group_id: str) -> None:
        """刷新消息面板为群聊。"""
        if not group_id:
            return
        conversation_id = self.chat_service.get_conversation_with_group(group_id)
        if not conversation_id:
            self.logger.warning(f"尝试设置不存在的群组: {group_id}")
            return
        self.chat_service.select_conversation(conversation_id)
        self.messages_panel.set_active_group(group_id)
        self.active_contact_id = None
        self.active_target = {
            "kind": "group",
            "id": group_id,
            "conversation_id": conversation_id,
        }
        if hasattr(self, "ai_panel"):
            self.ai_panel.set_active_target(self.active_target)
        self.logger.info(f"设置消息面板为群组: {group_id}")

    def connect_signals(self):
        """连接信号与槽"""
        # 聊天对象选中时，创建或切换到对应会话
        self.contacts_panel.target_selected.connect(self.on_target_selected)

        # 消息面板的发送信号不再直接处理，由面板自己处理

    @Slot(dict)
    def on_target_selected(self, target: dict):
        """联系人或群组被选中时的统一处理。"""
        kind = target.get("kind")
        target_id = target.get("id")
        if kind == "group":
            self._set_messages_panel_for_group(target_id)
        else:
            self.on_contact_selected(target_id)

    def subscribe_events(self):
        """订阅事件总线事件"""
        # 消息相关事件
        subscribe(EventType.MESSAGE_SENT, self.on_message_sent)
        subscribe(EventType.MESSAGE_RECEIVED, self.on_message_received)
        subscribe(EventType.MESSAGE_FAILED, self.on_message_failed)

        # 会话相关事件（已移除会话面板，保留事件处理以防其他组件使用）
        subscribe(EventType.CONVERSATION_UPDATED, self.on_conversation_updated)
        subscribe(EventType.CONVERSATION_CREATED, self.on_conversation_updated)

        # 系统事件
        subscribe(EventType.CONNECTING, self.on_connecting)
        subscribe(EventType.CONNECTED, self.on_connected)
        subscribe(EventType.DISCONNECTED, self.on_disconnected)
        subscribe(EventType.ERROR, self.on_error)
        subscribe(EventType.USER_STATUS_CHANGED, self.on_user_status_changed)
        subscribe(EventType.USER_UPDATED, self.on_user_updated)
        
        # 配对与信任事件
        subscribe(EventType.PAIRING_REQUEST, self.on_pairing_request)
        subscribe(EventType.PAIRING_COMPLETED, self.on_pairing_completed)
        subscribe(EventType.CONTACT_AUTH_REQUIRED, self.on_contact_auth_required)
        subscribe(EventType.GROUP_INVITE_RECEIVED, self.on_group_invite_received)

        self.logger.info("事件订阅完成")

    @Slot(str)
    def on_contact_selected(self, contact_id: str):
        """联系人被选中时的处理"""
        # 获取联系人信息
        contacts = self.chat_service.get_contacts()
        if contact_id in contacts:
            contact_name = self.chat_service.get_contact_display_name(contact_id)

            # 获取或创建会话ID
            conversation_id = self.chat_service.get_conversation_with_user(contact_id)
            if conversation_id:
                # 选择会话
                self.chat_service.select_conversation(conversation_id)
                # 设置消息面板
                self._set_messages_panel_for_contact(contact_id)
                self.logger.info(f"选择联系人: {contact_name} ({contact_id})")
                self.logger.debug(f"联系人选中后 active_contact_id: {self.active_contact_id}")
            else:
                self.logger.error(f"获取会话失败: {contact_id}")
        else:
            self.logger.warning(f"尝试选择不存在的联系人: {contact_id}")

    def _is_in_main_thread(self) -> bool:
        app = QApplication.instance()
        return app is None or QThread.currentThread() == app.thread()

    def _reroute_to_main_thread(self, handler_name: str, event: Event) -> bool:
        if self._is_in_main_thread():
            return False
        self.ui_event_signal.emit({"handler": handler_name, "event": event})
        return True

    @Slot(object)
    def _dispatch_ui_event(self, payload: object):
        data = payload if isinstance(payload, dict) else {}
        handler_name = data.get("handler")
        event = data.get("event")
        if not handler_name:
            return
        handler = getattr(self, handler_name, None)
        if handler is None:
            self.logger.error(f"未找到UI事件处理器: {handler_name}")
            return
        handler(event)



    def on_message_sent(self, event: Event):
        """消息发送事件处理"""
        if self._reroute_to_main_thread("on_message_sent", event):
            return
        
        message_data = event.data.get("message")

        if message_data:
            # 将字典转换为Message对象
            from src.models.message import Message
            message = Message.from_dict(message_data)

            if self.messages_panel.is_message_for_active_contact(message):
                self.messages_panel.add_message(message, is_self=True)
                self.logger.info(f"消息发送成功并显示: {message.id[:8]}")
            else:
                self.logger.info(f"消息发送成功（非当前会话）: {message.id[:8]}")

    def on_message_received(self, event: Event):
        """消息接收事件处理"""
        if self._reroute_to_main_thread("on_message_received", event):
            return
        self.logger.debug(f"消息接收事件: active_contact_id={self.active_contact_id}")
        
        message_data = event.data.get("message")

        if message_data:
            # 将字典转换为Message对象
            from src.models.message import Message
            message = Message.from_dict(message_data)

            # 获取发送者显示名称
            sender_name = self.chat_service.get_contact_display_name(message.sender_id)
            is_group_message = (
                message.metadata.get("conversation_type") == ConversationType.GROUP.value
                if isinstance(message.metadata, dict)
                else False
            ) or message.conversation_id.startswith("grp_")

            if self.messages_panel.is_message_for_active_contact(message):
                self.messages_panel.add_message(message, is_self=False)
                if is_group_message:
                    self.chat_service.select_conversation(message.conversation_id)
                else:
                    conversation_id = self.chat_service.get_conversation_with_user(message.sender_id)
                    if conversation_id:
                        self.chat_service.select_conversation(conversation_id)
                self.contacts_panel.load_contacts()
                self.logger.info(f"消息接收成功并显示: {message.id[:8]}")
            else:
                # 非当前会话消息，显示通知
                if is_group_message:
                    group_name = self.chat_service.get_group_display_name(message.conversation_id)
                    self._show_message_notification(group_name, message.content[:50])
                    self.logger.info(f"收到新群消息（非当前会话）: {message.id[:8]}，群 {group_name}")
                else:
                    self._show_message_notification(sender_name, message.content[:50])
                    self.logger.info(f"收到新消息（非当前会话）: {message.id[:8]}，来自 {sender_name}")
                
                # 更新会话未读计数
                self._update_contact_unread_count(message.sender_id)
    
    def _show_message_notification(self, sender_name: str, message_preview: str):
        """显示消息通知"""
        # 在状态栏显示临时消息
        self.status_bar.showMessage(f"新消息来自 {sender_name}: {message_preview}...", 5000)
        
        # 可选：播放提示音或系统通知
        # QApplication.beep()
    
    def _update_contact_unread_count(self, contact_id: str):
        """更新联系人未读计数（UI更新）"""
        # 重新加载联系人列表以更新未读计数显示
        self.contacts_panel.load_contacts()

    def on_message_failed(self, event: Event):
        """消息发送失败事件处理"""
        if self._reroute_to_main_thread("on_message_failed", event):
            return
        
        message_data = event.data.get("message")
        reason = event.data.get("reason", "未知原因")

        if message_data:
            from src.models.message import Message
            message = Message.from_dict(message_data)

            if self.messages_panel.is_message_for_active_contact(message):
                error_msg = f"消息发送失败: {reason}"
                self.messages_panel.messages_display.append(
                    f'<font color="red"><i>{error_msg}</i></font>'
                )
                self.logger.error(f"消息发送失败: {message.id[:8]}, 原因: {reason}")
            else:
                self.logger.error(f"消息发送失败（非当前会话）: {message.id[:8]}, 原因: {reason}")

    def on_conversation_selected_event(self, event: Event):
        """会话选中事件处理"""
        conversation_data = event.data.get("conversation")
        conversation_id = event.data.get("conversation_id")

        # 优先使用 conversation_id，否则从 conversation_data 中提取
        target_conversation_id = conversation_id
        if not target_conversation_id and conversation_data:
            target_conversation_id = conversation_data.get("id")
        
        if target_conversation_id:
            conversations = self.chat_service.get_conversations()
            conversation = conversations.get(target_conversation_id)
            if conversation:
                if conversation.conversation_type == ConversationType.GROUP:
                    self._set_messages_panel_for_group(conversation.id)
                else:
                    for participant_id in conversation.participant_ids:
                        if participant_id != self.current_user_id:
                            self._set_messages_panel_for_contact(participant_id)
                            break
            self.logger.info(f"会话选中事件处理: {target_conversation_id}")

    def on_conversation_updated(self, event: Event):
        """会话更新事件处理 - 更新联系人未读计数显示"""
        if self._reroute_to_main_thread("on_conversation_updated", event):
            return
        
        # 重新加载联系人列表以更新未读计数
        self.contacts_panel.load_contacts()
        self.logger.debug("会话更新，联系人列表已刷新")

    def on_conversation_created(self, event: Event):
        """会话创建事件处理"""
        # 会话面板已移除，无需处理
        pass

    def on_connecting(self, event: Event):
        """连接中事件处理"""
        if self._reroute_to_main_thread("on_connecting", event):
            return
        self.logger.info("聊天服务正在连接...")
        self.update_network_status("connecting")

    def on_connected(self, event: Event):
        """连接成功事件处理"""
        if self._reroute_to_main_thread("on_connected", event):
            return
        self.logger.info("聊天服务连接成功")
        self.update_network_status("connected")
        self._schedule_presence_refresh()

    def on_disconnected(self, event: Event):
        """断开连接事件处理"""
        if self._reroute_to_main_thread("on_disconnected", event):
            return
        self.logger.info("聊天服务断开连接")
        self.update_network_status("disconnected")

    def on_error(self, event: Event):
        """错误事件处理"""
        if self._reroute_to_main_thread("on_error", event):
            return
        error_msg = event.data.get("message", "未知错误")
        self.logger.error(f"系统错误: {error_msg}")
        # 可以显示错误对话框

    def on_user_status_changed(self, event: Event):
        """在线状态变更事件处理。"""
        if self._reroute_to_main_thread("on_user_status_changed", event):
            return
        self.contacts_panel.load_contacts()
        self.logger.debug("用户在线状态变化，联系人列表已刷新")

    def on_user_updated(self, event: Event):
        """用户资料变更事件处理。"""
        if self._reroute_to_main_thread("on_user_updated", event):
            return
        self.contacts_panel.load_contacts()
        self.logger.debug("用户资料变化，联系人列表已刷新")

    def on_pairing_request(self, event: Event):
        """配对请求事件处理（可能从后台线程调用）"""
        request_id = event.data.get("request_id")
        user_id = event.data.get("user_id")
        device_id = event.data.get("device_id")
        fingerprint = event.data.get("fingerprint", "未知")
        
        if not request_id or not user_id or not device_id:
            self.logger.error("配对请求缺少必要参数")
            return
        
        # 通过信号调度到主线程
        self.pairing_request_signal.emit({
            "request_id": request_id,
            "user_id": user_id,
            "device_id": device_id,
            "fingerprint": fingerprint
        })
    
    def _show_pairing_dialog(self, data: dict):
        """在主线程显示配对对话框（由信号触发）"""
        from src.identity.pairing import PairingResult
        
        request_id = data.get("request_id")
        user_id = data.get("user_id")
        device_id = data.get("device_id")
        fingerprint = data.get("fingerprint", "未知")
        
        if not self.isVisible():
            self.logger.debug("窗口未显示，延迟处理配对请求")
            QTimer.singleShot(500, lambda: self._show_pairing_dialog(data))
            return
        
        reply = QMessageBox.question(
            self,
            "配对请求",
            f"收到来自用户 {user_id} 的配对请求。\n"
            f"设备ID: {device_id}\n"
            f"指纹: {fingerprint}\n\n"
            f"是否信任此联系人？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Yes
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            result = PairingResult.TRUSTED
        elif reply == QMessageBox.StandardButton.No:
            result = PairingResult.REJECTED
        else:
            result = PairingResult.CANCELLED
        
        success = self.chat_service.pairing_manager.handle_pairing_response(request_id, result)
        
        if success:
            self.logger.info(f"配对请求处理完成: {request_id} -> {result.value}")
        else:
            self.logger.error(f"配对请求处理失败: {request_id}")

    def on_pairing_completed(self, event: Event):
        """配对完成事件处理"""
        if self._reroute_to_main_thread("on_pairing_completed", event):
            return
        request_id = event.data.get("request_id")
        result = event.data.get("result")
        user_id = event.data.get("user_id")
        self.logger.info(f"配对完成: {request_id} -> {result}")
        # 更新UI
        if user_id:
            self._refresh_ui_after_authorization(user_id)

    def _refresh_ui_after_authorization(self, contact_id: str):
        """联系人授权后的UI刷新"""
        # 刷新联系人列表（更新授权状态显示）
        self.contacts_panel.load_contacts()
        
        # 如果当前活跃联系人正是被授权的联系人，刷新会话显示
        if self.active_contact_id == contact_id:
            self._refresh_current_conversation()

    def _refresh_current_conversation(self):
        """刷新当前会话的显示（例如授权后重新加载消息）"""
        if self.active_target and self.active_target.get("kind") == "group":
            self._set_messages_panel_for_group(self.active_target.get("id", ""))
        elif self.active_contact_id:
            self._set_messages_panel_for_contact(self.active_contact_id)

    def on_contact_auth_required(self, event: Event):
        """联系人授权请求事件处理（可能从后台线程调用）"""
        contact_id = event.data.get("contact_id")
        message_id = event.data.get("message_id")
        message_preview = event.data.get("message_preview", "")
        timestamp = event.data.get("timestamp")
        pending_message_count = event.data.get("pending_message_count", 0)
        
        if not contact_id:
            self.logger.error("联系人授权请求缺少必要参数: contact_id")
            return
        
        self.logger.info(f"收到联系人授权请求事件: contact_id={contact_id}, preview={message_preview}")
        
        # 通过信号调度到主线程，避免QTimer.singleShot在后台线程无效的问题
        self.contact_auth_required_signal.emit({
            "contact_id": contact_id,
            "message_id": message_id,
            "message_preview": message_preview,
            "timestamp": timestamp,
            "pending_message_count": pending_message_count,
        })

    def on_group_invite_received(self, event: Event):
        """群邀请事件处理（可能从后台线程调用）。"""
        group = event.data.get("group", {})
        inviter_id = event.data.get("inviter_id", "")
        members = event.data.get("members", [])
        if not isinstance(group, dict) or not group.get("id"):
            self.logger.error("群邀请缺少必要参数")
            return
        self.group_invite_received_signal.emit({
            "group": group,
            "inviter_id": inviter_id,
            "members": members,
        })

    def _show_group_invite_dialog(self, data: dict):
        """在主线程显示群邀请验证对话框。"""
        group = data.get("group", {})
        inviter_id = data.get("inviter_id", "")
        group_id = group.get("id", "")
        group_name = group.get("name") or group_id
        if not group_id:
            return

        if not self.isVisible():
            QTimer.singleShot(500, lambda: self._show_group_invite_dialog(data))
            return

        inviter_name = self.chat_service.get_contact_display_name(inviter_id) if inviter_id else "未知用户"
        reply = QMessageBox.question(
            self,
            "群组邀请",
            f"{inviter_name} 邀请你加入群组：{group_name}\n\n是否接受？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )

        if reply == QMessageBox.StandardButton.Yes:
            if self.chat_service.accept_group_invite(group_id):
                self.contacts_panel.load_contacts()
                self._set_messages_panel_for_group(group_id)
                self.status_bar.showMessage(f"已加入群组 {group_name}", 3000)
            else:
                QMessageBox.warning(self, "加入失败", "群邀请已失效或无法验证")
        else:
            self.chat_service.reject_group_invite(group_id)
            self.contacts_panel.load_contacts()
            self.status_bar.showMessage(f"已拒绝群组 {group_name}", 3000)
    
    def _show_contact_auth_dialog(self, data: dict):
        """在主线程显示联系人授权对话框（由信号触发）"""
        contact_id = data.get("contact_id")
        message_id = data.get("message_id")
        message_preview = data.get("message_preview", "")
        timestamp = data.get("timestamp")
        pending_message_count = data.get("pending_message_count", 0)
        
        if not contact_id:
            self.logger.error("联系人授权请求缺少必要参数: contact_id")
            return
        
        self.logger.info(f"准备显示授权对话框: contact_id={contact_id}")
        
        # 如果窗口还未显示，延迟显示对话框
        if not self.isVisible():
            self.logger.debug("窗口未显示，延迟处理授权请求")
            QTimer.singleShot(500, lambda: self._show_contact_auth_dialog(data))
            return
        
        # 显示联系人授权确认对话框
        identity_manager = self.chat_service.identity_manager
        contact = identity_manager.get_contact(contact_id)
        contact_name = contact.get_display_name() if contact else contact_id
        
        self.logger.info(f"显示授权对话框: contact_name={contact_name}, preview={message_preview}")
        
        try:
            reply = QMessageBox.question(
                self,
                "联系人授权请求",
                f"收到来自 {contact_name} 的消息:\n"
                f"\"{message_preview}\"\n\n"
                f"当前待处理消息数: {pending_message_count or 1}\n\n"
                f"是否授权此联系人并显示消息？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Yes
            )
            
            self.logger.info(f"授权对话框返回值: {reply}")
            
            if reply == QMessageBox.StandardButton.Yes:
                if self.chat_service.accept_contact(contact_id):
                    self.logger.info(f"联系人已授权: {contact_id}")
                    self._refresh_ui_after_authorization(contact_id)
                else:
                    QMessageBox.warning(self, "授权失败", "联系人授权失败，请重试")
            elif reply == QMessageBox.StandardButton.No:
                if self.chat_service.reject_contact(contact_id):
                    self.logger.info(f"联系人已被拒绝: {contact_id}")
                    self._refresh_ui_after_authorization(contact_id)
                else:
                    QMessageBox.warning(self, "拒绝失败", "联系人拒绝失败，请重试")
            else:
                # 取消，保持待授权状态
                self.logger.info(f"联系人授权请求被取消: {contact_id}")
        except Exception as e:
            self.logger.error(f"显示授权对话框失败: {e}", exc_info=True)


if __name__ == "__main__":
    # 测试主窗口
    app = QApplication(sys.argv)
    window = MainWindow(current_user_id="test_user")
    window.show()
    sys.exit(app.exec())
