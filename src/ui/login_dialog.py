"""
最小登录/初始化对话框。

M3 起仅保留两种用户可见状态：
1. 初始化：显示只读 user_id + 密码 + 确认密码 + 可选 display_name
2. 登录：显示只读 user_id + 密码
"""

from typing import Optional, Tuple

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.identity.local_identity import IdentityManager, get_global_identity_manager
from src.utils.logger import get_logger


class LoginDialog(QDialog):
    """最小本地密码登录对话框。"""

    MODE_INITIALIZE = "initialize"
    MODE_LOGIN = "login"

    login_success = Signal(str, str)

    def __init__(
        self,
        config_dir: str = "data/config",
        identity_manager: Optional[IdentityManager] = None,
        preferred_user_id: Optional[str] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.config_dir = config_dir
        self.logger = get_logger("login_dialog")
        self.identity_manager = identity_manager or get_global_identity_manager(config_dir)
        self.preferred_user_id = (preferred_user_id or "").strip()

        self.mode = self.MODE_LOGIN
        self.user_id = ""
        self.display_name = ""

        self.init_ui()
        self.configure_mode()

        self.setWindowTitle("即时聊天系统")
        self.setMinimumWidth(420)

    def init_ui(self) -> None:
        layout = QVBoxLayout(self)

        title_label = QLabel("即时聊天系统")
        title_font = QFont()
        title_font.setBold(True)
        title_font.setPointSize(16)
        title_label.setFont(title_font)
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title_label)

        self.mode_hint_label = QLabel("")
        self.mode_hint_label.setWordWrap(True)
        self.mode_hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.mode_hint_label)

        layout.addSpacing(16)

        form_layout = QFormLayout()

        self.user_id_input = QLineEdit()
        self.user_id_input.setReadOnly(True)
        self.user_id_input.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        form_layout.addRow("我的 ID:", self.user_id_input)

        self.password_input = QLineEdit()
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_input.setPlaceholderText("请输入密码")
        form_layout.addRow("密码:", self.password_input)

        self.confirm_password_label = QLabel("确认密码:")
        self.confirm_password_input = QLineEdit()
        self.confirm_password_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.confirm_password_input.setPlaceholderText("再次输入密码")
        form_layout.addRow(self.confirm_password_label, self.confirm_password_input)

        self.display_name_label = QLabel("显示名称:")
        self.display_name_input = QLineEdit()
        self.display_name_input.setPlaceholderText("可选，仅用于界面显示")
        form_layout.addRow(self.display_name_label, self.display_name_input)

        self.show_password_check = QCheckBox("显示密码")
        self.show_password_check.toggled.connect(self.on_show_password_toggled)
        form_layout.addRow("", self.show_password_check)

        layout.addLayout(form_layout)
        layout.addSpacing(16)

        button_layout = QHBoxLayout()

        self.submit_button = QPushButton("登录")
        self.submit_button.setDefault(True)
        self.submit_button.clicked.connect(self.on_submit_clicked)
        button_layout.addWidget(self.submit_button)

        cancel_button = QPushButton("取消")
        cancel_button.clicked.connect(self.reject)
        button_layout.addWidget(cancel_button)

        layout.addLayout(button_layout)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

    def configure_mode(self) -> None:
        """根据本地 identity 状态切换初始化/登录模式。"""
        if self.identity_manager.load_existing_identity():
            current_user = self.identity_manager.get_current_user()
            if current_user is None:
                raise ValueError("读取本地 identity 失败")

            if self.preferred_user_id and current_user.user_id != self.preferred_user_id:
                raise ValueError(
                    f"当前数据目录已固定 user_id 为 {current_user.user_id}，与请求的 {self.preferred_user_id} 不一致"
                )

            self.user_id = current_user.user_id
            self.display_name = current_user.display_name
            self.user_id_input.setText(self.user_id)
            self.display_name_input.setText(self.display_name)

            if self.identity_manager.has_password():
                self.mode = self.MODE_LOGIN
                self.mode_hint_label.setText("请输入本地密码登录。")
            else:
                self.mode = self.MODE_INITIALIZE
                self.mode_hint_label.setText("检测到已有 identity，但尚未设置本地密码。请完成初始化。")
        else:
            self.mode = self.MODE_INITIALIZE
            self.user_id = self.preferred_user_id or self.identity_manager.generate_user_id()
            self.display_name = ""
            self.user_id_input.setText(self.user_id)
            self.display_name_input.clear()
            self.mode_hint_label.setText("首次初始化：请设置本地密码，可选填写显示名称。")

        self._apply_mode_visibility()
        self.password_input.clear()
        self.confirm_password_input.clear()
        self.status_label.clear()
        self.password_input.setFocus()

    def _apply_mode_visibility(self) -> None:
        is_initialize = self.mode == self.MODE_INITIALIZE
        self.confirm_password_label.setVisible(is_initialize)
        self.confirm_password_input.setVisible(is_initialize)
        self.display_name_label.setVisible(is_initialize)
        self.display_name_input.setVisible(is_initialize)
        self.submit_button.setText("初始化并进入" if is_initialize else "登录")

    def on_show_password_toggled(self, checked: bool) -> None:
        echo_mode = QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
        self.password_input.setEchoMode(echo_mode)
        self.confirm_password_input.setEchoMode(echo_mode)

    def on_submit_clicked(self) -> None:
        password = self.password_input.text()
        current_user_id = self.user_id_input.text().strip()

        if not current_user_id:
            self.show_error("缺少本地 user_id")
            return

        if not password:
            self.show_error("请输入密码")
            return

        if self.mode == self.MODE_INITIALIZE:
            confirm_password = self.confirm_password_input.text()
            if password != confirm_password:
                self.show_error("两次输入的密码不一致")
                return

            display_name = self.display_name_input.text().strip()
            if not self.identity_manager.initialize_identity(current_user_id, password, display_name):
                self.show_error("初始化 identity 失败")
                return
        else:
            if not self.identity_manager.verify_password(password):
                self.show_error("密码不正确")
                return

        self.accept_login()

    def accept_login(self) -> None:
        current_user = self.identity_manager.get_current_user()
        if not current_user:
            self.show_error("无法读取当前用户信息")
            return

        self.user_id = current_user.user_id
        self.display_name = current_user.display_name

        self.logger.info(f"Login accepted: {self.user_id}")
        self.login_success.emit(self.user_id, self.display_name)
        self.accept()

    def show_error(self, message: str) -> None:
        self.status_label.setText(f'<font color="red">{message}</font>')
        self.logger.error(f"Login error: {message}")

    def get_result(self) -> Tuple[str, str]:
        """返回登录结果。"""
        return self.user_id, self.display_name


def show_login_dialog(
    config_dir: str = "data/config",
    identity_manager: Optional[IdentityManager] = None,
    preferred_user_id: Optional[str] = None,
    parent: Optional[QWidget] = None,
) -> Tuple[str, str]:
    """
    显示登录对话框并返回登录后的 (user_id, display_name)。
    """
    dialog = LoginDialog(
        config_dir=config_dir,
        identity_manager=identity_manager,
        preferred_user_id=preferred_user_id,
        parent=parent,
    )
    if dialog.exec() == QDialog.DialogCode.Accepted:
        return dialog.get_result()
    raise ValueError("Login cancelled by user")
