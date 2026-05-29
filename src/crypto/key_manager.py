"""
密钥管理器

管理本地测试密钥/预共享密钥（PSK），为未来替换成真正密钥对方案预留接口。
当前阶段使用固定测试密钥验证加密链路。
"""

import hashlib
import secrets
from typing import Dict, Optional


class KeyManager:
    """
    密钥管理器

    职责：
    1. 管理本地测试密钥/预共享密钥
    2. 提供获取密钥接口
    3. 为未来替换成真正密钥对方案预留接口
    """

    def __init__(self):
        self._keys: Dict[str, bytes] = {}  # user_id -> key_bytes
        self._default_key: Optional[bytes] = None
        
        # 初始化时生成一个默认测试密钥（仅用于开发测试）
        self._init_test_key()

    def _init_test_key(self) -> None:
        """初始化测试密钥（仅用于开发阶段）"""
        # 使用固定测试密钥，便于开发测试
        # 在实际应用中，应该使用安全的密钥协商协议生成密钥
        test_key_seed = "instant_messaging_team_test_key_v1"
        test_key = hashlib.sha256(test_key_seed.encode()).digest()
        
        self._default_key = test_key
        self._keys["default"] = test_key
        
        # 不再为预定义测试用户生成密钥，避免用户特异化逻辑
        # 密钥将按需动态生成

    def get_key_for_user(self, user_id: str) -> bytes:
        """
        获取用于加密/解密指定用户消息的密钥

        Args:
            user_id: 用户ID

        Returns:
            密钥字节（32字节，适用于AES-256）

        Note:
            当前阶段返回预共享密钥，未来应该返回从密钥协商协议派生的密钥
        """
        if user_id in self._keys:
            return self._keys[user_id]
        
        # 如果用户没有特定密钥，使用默认密钥
        if self._default_key is not None:
            # 为新用户生成派生密钥
            derived_key = hashlib.sha256(
                self._default_key + f":{user_id}".encode()
            ).digest()
            self._keys[user_id] = derived_key
            return derived_key
        
        # 如果没有默认密钥，生成一个新的随机密钥（不应该发生）
        new_key = secrets.token_bytes(32)
        self._keys[user_id] = new_key
        return new_key

    def get_key_fingerprint(self, user_id: str) -> str:
        """
        获取指定用户密钥的指纹

        Args:
            user_id: 用户ID

        Returns:
            密钥指纹字符串（SHA256哈希的前8个字符）
        """
        key = self.get_key_for_user(user_id)
        fingerprint = hashlib.sha256(key).hexdigest()[:8]
        return fingerprint

    def has_key_for_user(self, user_id: str) -> bool:
        """
        检查是否拥有指定用户的密钥

        Args:
            user_id: 用户ID

        Returns:
            bool: 是否拥有该用户的密钥
        """
        return user_id in self._keys or self._default_key is not None

    def import_key(self, user_id: str, key_bytes: bytes) -> None:
        """
        导入密钥（用于预共享密钥或未来密钥协商）

        Args:
            user_id: 用户ID
            key_bytes: 密钥字节
        """
        if len(key_bytes) not in [16, 24, 32]:
            raise ValueError(f"密钥长度必须为16、24或32字节，实际为{len(key_bytes)}字节")
        
        self._keys[user_id] = key_bytes

    def remove_key(self, user_id: str) -> bool:
        """
        移除指定用户的密钥

        Args:
            user_id: 用户ID

        Returns:
            bool: 是否成功移除
        """
        if user_id in self._keys:
            del self._keys[user_id]
            return True
        return False

    def clear_all_keys(self) -> None:
        """清除所有密钥（危险操作，仅用于测试）"""
        self._keys.clear()
        self._default_key = None
        self._init_test_key()