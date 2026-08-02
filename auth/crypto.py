import os
import bcrypt
import base64
import hashlib
import logging

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes as _crypto_hashes

from django.core.exceptions import ImproperlyConfigured

logger = logging.getLogger(__name__)


def HashPassword(password, rounds=12):
    """哈希密码——返回 str（与 User.SetPassword 存储格式一致）"""
    if isinstance(password, str):
        password = password.encode('utf-8')
    return bcrypt.hashpw(password, bcrypt.gensalt(rounds=rounds)).decode('utf-8')


def VerifyPassword(password, hashed):
    if isinstance(password, str):
        password = password.encode('utf-8')
    if isinstance(hashed, str):
        hashed = hashed.encode('utf-8')
    return bcrypt.checkpw(password, hashed)


def _derive_key(master_secret):
    """使用 HKDF-SHA256 从任意长度的主密钥派生 256-bit AES 密钥

    salt 来源优先级：
      1. settings.GVSDSDK_CRYPTO_SALT（bytes，推荐生产环境配置）
      2. 默认值 b'gvsds-crypto-salt'（仅用于开发环境，会记录警告）
    """
    if isinstance(master_secret, str):
        master_secret = master_secret.encode('utf-8')
    # 尝试从 settings 读取可配置 salt
    salt = b'gvsds-crypto-salt'
    try:
        from django.conf import settings
        configured_salt = getattr(settings, 'GVSDSDK_CRYPTO_SALT', None)
        if configured_salt:
            salt = configured_salt if isinstance(configured_salt, bytes) else str(configured_salt).encode('utf-8')
    except (ImproperlyConfigured, ImportError):
        pass
    if salt == b'gvsds-crypto-salt':
        # 生产环境可通过 settings.GVSDSDK_REQUIRE_CUSTOM_SALT=True 强制要求自定义 salt
        try:
            from django.conf import settings
            require_custom = getattr(settings, 'GVSDSDK_REQUIRE_CUSTOM_SALT', False)
        except (ImproperlyConfigured, ImportError):
            require_custom = False
        if require_custom:
            raise ImproperlyConfigured(
                '生产环境必须配置自定义 HKDF salt，请在 settings 中设置 GVSDSDK_CRYPTO_SALT'
            )
        logger.warning('使用默认 crypto salt，生产环境必须配置 settings.GVSDSDK_CRYPTO_SALT')
    hkdf = HKDF(
        algorithm=_crypto_hashes.SHA256(),
        length=32,
        salt=salt,
        info=b'aes-gcm-256',
    )
    return hkdf.derive(master_secret)


def SymmetricEncrypt(plaintext, master_secret):
    """使用 AES-256-GCM 加密

    输出格式: base64(nonce_12 + ciphertext + tag_16)
    """
    if isinstance(plaintext, str):
        plaintext = plaintext.encode('utf-8')
    key = _derive_key(master_secret)
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)
    ciphertext_with_tag = aesgcm.encrypt(nonce, plaintext, None)
    return base64.b64encode(nonce + ciphertext_with_tag).decode('ascii')


def SymmetricDecrypt(ciphertext_b64, master_secret):
    """使用 AES-256-GCM 解密

    输入格式: base64(nonce_12 + ciphertext + tag_16)
    """
    raw = base64.b64decode(ciphertext_b64)
    nonce = raw[:12]
    ciphertext_with_tag = raw[12:]
    key = _derive_key(master_secret)
    aesgcm = AESGCM(key)
    plaintext = aesgcm.decrypt(nonce, ciphertext_with_tag, None)
    return plaintext.decode('utf-8')


def _FileHash(filepath, algorithm):
    """计算文件哈希的公共实现——按 algorithm 名称创建 hash 对象，分块读取文件"""
    h = hashlib.new(algorithm)
    with open(filepath, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            h.update(chunk)
    return h.hexdigest()


def FileSHA1(filepath):
    """计算文件 SHA-1 哈希。注意：SHA-1 不适用于安全场景（如签名验证、防篡改），仅用于文件去重等非安全用途。安全场景请使用 FileSHA256。"""
    return _FileHash(filepath, 'sha1')


def FileSHA256(filepath):
    return _FileHash(filepath, 'sha256')


def BytesSHA1(data):
    """计算字节流 SHA-1 哈希。注意：SHA-1 不适用于安全场景，仅用于非安全用途（如缓存键、去重标识）。安全场景请使用 BytesSHA256。"""
    return hashlib.sha1(data).hexdigest()


def BytesSHA256(data):
    return hashlib.sha256(data).hexdigest()
