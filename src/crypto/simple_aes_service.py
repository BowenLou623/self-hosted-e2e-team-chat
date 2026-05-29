"""
简单AES加密服务

使用AES-GCM算法实现对称加密，提供最小可用的加密闭环。
当前阶段使用预共享密钥（PSK）验证加密链路。
"""

import base64
import json
import time
from typing import Dict, Any

from src.crypto.interface import CryptoService, DecryptionError
from src.crypto.key_manager import KeyManager


class SimpleAESService(CryptoService):
    """
    简单AES加密服务

    使用AES-GCM算法实现端到端加密：
    1. 使用预共享密钥（当前阶段）或协商密钥
    2. 加密消息内容，生成包含密文和随机数的元数据
    3. 验证消息完整性和真实性

    注意：当前阶段使用固定测试密钥，仅用于验证加密链路。
          在实际应用中，应该使用安全的密钥协商协议。
    """

    ENCRYPTION_VERSION = "v1_aes_gcm_psk"
    AES_KEY_SIZE = 32  # AES-256
    NONCE_SIZE = 12    # GCM推荐12字节随机数

    def __init__(self, key_manager: KeyManager):
        """
        初始化加密服务

        Args:
            key_manager: 密钥管理器实例
        """
        self.key_manager = key_manager
        
        # 尝试导入cryptography库
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            self.AESGCM = AESGCM
        except ImportError:
            raise ImportError(
                "请安装cryptography库：pip install cryptography\n"
                "或者使用系统包管理器安装python3-cryptography"
            )

    def encrypt_message(self, plaintext: str, sender_id: str, recipient_id: str) -> Dict[str, Any]:
        """
        加密消息

        Args:
            plaintext: 明文消息内容
            sender_id: 发送者用户ID（当前阶段未使用，预留）
            recipient_id: 接收者用户ID

        Returns:
            加密元数据字典，包含：
            - ciphertext: Base64编码的密文
            - nonce: Base64编码的随机数
            - encryption_version: 加密算法版本标识
            - key_fingerprint: 使用的密钥指纹
            - timestamp: 加密时间戳
            - recipient_id: 接收者ID
        """
        # 获取接收者的密钥
        key = self.key_manager.get_key_for_user(recipient_id)
        
        # 生成随机数
        import secrets
        nonce = secrets.token_bytes(self.NONCE_SIZE)
        
        # 创建AES-GCM实例并加密
        aesgcm = self.AESGCM(key)
        ciphertext = aesgcm.encrypt(nonce, plaintext.encode(), None)
        
        # 获取密钥指纹
        key_fingerprint = self.key_manager.get_key_fingerprint(recipient_id)
        
        # 构建加密元数据
        metadata = {
            "ciphertext": base64.b64encode(ciphertext).decode('utf-8'),
            "nonce": base64.b64encode(nonce).decode('utf-8'),
            "encryption_version": self.ENCRYPTION_VERSION,
            "key_fingerprint": key_fingerprint,
            "timestamp": time.time(),
            "recipient_id": recipient_id,
        }
        
        return metadata

    def decrypt_message(self, metadata: Dict[str, Any], recipient_id: str) -> str:
        """
        解密消息

        Args:
            metadata: 加密元数据字典
            recipient_id: 接收者用户ID（用于验证）

        Returns:
            解密后的明文消息内容

        Raises:
            DecryptionError: 当解密失败时抛出
        """
        # 验证元数据必需字段
        required_fields = ["ciphertext", "nonce", "encryption_version", "key_fingerprint"]
        for field in required_fields:
            if field not in metadata:
                raise DecryptionError(f"缺少必需字段: {field}")
        
        # 验证加密版本
        if metadata["encryption_version"] != self.ENCRYPTION_VERSION:
            raise DecryptionError(
                f"不支持的加密版本: {metadata['encryption_version']}, "
                f"期望: {self.ENCRYPTION_VERSION}"
            )
        
        # 验证接收者ID（如果提供）
        if "recipient_id" in metadata and metadata["recipient_id"] != recipient_id:
            raise DecryptionError(
                f"消息接收者不匹配: 期望{recipient_id}, 实际{metadata['recipient_id']}"
            )
        
        # 获取接收者的密钥
        key = self.key_manager.get_key_for_user(recipient_id)
        
        # 验证密钥指纹（可选，增加安全性）
        expected_fingerprint = self.key_manager.get_key_fingerprint(recipient_id)
        if metadata["key_fingerprint"] != expected_fingerprint:
            raise DecryptionError(
                f"密钥指纹不匹配: 期望{expected_fingerprint}, "
                f"实际{metadata['key_fingerprint']}"
            )
        
        # 解码Base64数据
        try:
            ciphertext = base64.b64decode(metadata["ciphertext"])
            nonce = base64.b64decode(metadata["nonce"])
        except (ValueError, TypeError) as e:
            raise DecryptionError(f"Base64解码失败: {e}")
        
        # 验证随机数长度
        if len(nonce) != self.NONCE_SIZE:
            raise DecryptionError(f"随机数长度无效: 期望{self.NONCE_SIZE}, 实际{len(nonce)}")
        
        # 创建AES-GCM实例并解密
        try:
            aesgcm = self.AESGCM(key)
            plaintext_bytes = aesgcm.decrypt(nonce, ciphertext, None)
            plaintext = plaintext_bytes.decode('utf-8')
        except Exception as e:
            raise DecryptionError(f"解密失败: {e}")
        
        return plaintext

    def get_key_fingerprint(self) -> str:
        """
        获取当前使用的默认密钥指纹

        Returns:
            密钥指纹字符串
        """
        # 返回默认用户的密钥指纹（用于标识当前客户端使用的密钥）
        return self.key_manager.get_key_fingerprint("default")

    def get_encryption_version(self) -> str:
        """
        获取加密算法版本标识

        Returns:
            版本标识字符串
        """
        return self.ENCRYPTION_VERSION

    def is_version_supported(self, version: str) -> bool:
        """
        检查是否支持指定的加密版本

        Args:
            version: 要检查的版本字符串

        Returns:
            bool: 是否支持该版本
        """
        # 当前只支持一个版本，未来可以扩展支持多个版本
        return version == self.ENCRYPTION_VERSION

    def can_decrypt_for(self, recipient_id: str) -> bool:
        """
        检查是否能够解密发送给指定接收者的消息

        Args:
            recipient_id: 接收者用户ID

        Returns:
            bool: 是否能够解密
        """
        return self.key_manager.has_key_for_user(recipient_id)

    @classmethod
    def serialize_metadata(cls, metadata: Dict[str, Any]) -> str:
        """
        序列化加密元数据为JSON字符串

        Args:
            metadata: 加密元数据字典

        Returns:
            JSON字符串
        """
        return json.dumps(metadata)

    @classmethod
    def deserialize_metadata(cls, json_str: str) -> Dict[str, Any]:
        """
        从JSON字符串反序列化加密元数据

        Args:
            json_str: JSON字符串

        Returns:
            加密元数据字典
        """
        return json.loads(json_str)