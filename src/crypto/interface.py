"""
加密服务接口定义

定义统一的加密服务接口，支持不同的加密实现（如AES-GCM、Signal协议等）。
当前阶段实现最小可用的对称加密，未来可替换为更安全的非对称加密。
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional


class CryptoService(ABC):
    """
    加密服务抽象接口

    职责：
    1. 加密消息（明文 -> 加密元数据）
    2. 解密消息（加密元数据 -> 明文）
    3. 管理密钥相关信息
    """

    @abstractmethod
    def encrypt_message(self, plaintext: str, sender_id: str, recipient_id: str) -> Dict[str, Any]:
        """
        加密消息

        Args:
            plaintext: 明文消息内容
            sender_id: 发送者用户ID
            recipient_id: 接收者用户ID

        Returns:
            加密元数据字典，包含：
            - ciphertext: Base64编码的密文
            - nonce: Base64编码的随机数（用于AES-GCM等算法）
            - encryption_version: 加密算法版本标识
            - key_fingerprint: 使用的密钥指纹
            - timestamp: 加密时间戳
            - recipient_id: 接收者ID（冗余，便于验证）
        """
        pass

    @abstractmethod
    def decrypt_message(self, metadata: Dict[str, Any], recipient_id: str) -> str:
        """
        解密消息

        Args:
            metadata: 加密元数据字典（必须包含ciphertext、nonce等字段）
            recipient_id: 接收者用户ID（用于验证和密钥选择）

        Returns:
            解密后的明文消息内容

        Raises:
            DecryptionError: 当解密失败（如密文被篡改、密钥不匹配等）
        """
        pass

    @abstractmethod
    def get_key_fingerprint(self) -> str:
        """
        获取当前使用的密钥指纹

        Returns:
            密钥指纹字符串（如SHA256哈希的前8个字符）
        """
        pass

    @abstractmethod
    def get_encryption_version(self) -> str:
        """
        获取加密算法版本标识

        Returns:
            版本标识字符串（如"v1_aes_gcm_psk"）
        """
        pass

    @abstractmethod
    def can_decrypt_for(self, recipient_id: str) -> bool:
        """
        检查是否能够解密发送给指定接收者的消息

        Args:
            recipient_id: 接收者用户ID

        Returns:
            bool: 是否能够解密（即是否拥有对应的密钥）
        """
        pass


class DecryptionError(Exception):
    """解密失败异常"""
    pass