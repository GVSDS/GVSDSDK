"""统一 OSS 云存储抽象层。

支持三种后端，通过 app.ini [storage] 段 BACKEND 切换：
    - local: 本地磁盘存储（默认，零依赖）
    - cos:   腾讯云 COS（需 cos-python-sdk-v5）
    - oss:   阿里云 OSS（需 oss2）

统一 API（帕斯卡命名）：
    from gvsdsdk.storage import Storage
    url = Storage.Upload('assets/2024/abc.jpg', file_bytes, ContentType='image/jpeg')
    url = Storage.UploadFile('assets/2024/abc.jpg', file_object, ContentType='image/jpeg')
    Storage.Delete('assets/2024/abc.jpg')
    url = Storage.SignedUrl('assets/2024/abc.jpg', Expires=3600)
    exists = Storage.Exists('assets/2024/abc.jpg')
"""

import os
import logging
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from gvsdsdk.config import Get as _config_get

__all__ = ['Storage']

Logger = logging.getLogger('gvsds.storage')


def _NormalizeKey(Key):
    """规范化 key：去除前导斜杠，防止 .. 路径穿越。"""
    if not Key or not isinstance(Key, str):
        raise ValueError('key 必须是非空字符串')
    Key = Key.lstrip('/')
    Norm = os.path.normpath(Key).replace('\\', '/')
    Parts = Norm.split('/')
    if '..' in Parts or Norm.startswith('/') or Norm == '.':
        raise ValueError(f'非法 key（路径穿越）: {Key}')
    return Norm


class StorageBackend:
    """存储后端基类。"""

    def Upload(self, Key, Data, ContentType=None):
        raise NotImplementedError

    def Delete(self, Key):
        raise NotImplementedError

    def SignedUrl(self, Key, Expires=3600):
        raise NotImplementedError

    def Exists(self, Key):
        raise NotImplementedError


class LocalBackend(StorageBackend):
    """本地磁盘存储后端。"""

    def __init__(self):
        BaseDir = _config_get('storage', 'LOCAL_BASE_DIR')
        self.BaseDir = Path(BaseDir) if BaseDir else Path(settings.MEDIA_ROOT)
        BaseUrl = _config_get('storage', 'LOCAL_BASE_URL')
        self.BaseUrl = BaseUrl if BaseUrl else settings.MEDIA_URL
        self.BaseDir.mkdir(parents=True, exist_ok=True)

    def Upload(self, Key, Data, ContentType=None):
        try:
            Key = _NormalizeKey(Key)
            if hasattr(Data, 'read'):
                Data = Data.read()
            if not isinstance(Data, (bytes, bytearray, memoryview)):
                raise ValueError('Data 必须是 bytes 或 file-like 对象')
            FilePath = self.BaseDir / Key
            FilePath.parent.mkdir(parents=True, exist_ok=True)
            FilePath.write_bytes(bytes(Data))
            return self._BuildUrl(Key)
        except Exception as e:
            Logger.exception('LocalBackend Upload 失败: key=%s', Key)
            raise RuntimeError(f'本地存储上传失败: {e}') from e

    def Delete(self, Key):
        try:
            Key = _NormalizeKey(Key)
            FilePath = self.BaseDir / Key
            # 解析后确认仍在 BaseDir 内，双重防穿越
            Resolved = FilePath.resolve()
            if not str(Resolved).startswith(str(self.BaseDir.resolve())):
                raise ValueError(f'非法 key（越界）: {Key}')
            if FilePath.exists() and FilePath.is_file():
                FilePath.unlink()
        except Exception as e:
            Logger.exception('LocalBackend Delete 失败: key=%s', Key)
            raise RuntimeError(f'本地存储删除失败: {e}') from e

    def SignedUrl(self, Key, Expires=3600):
        # local 后端直接返回公开 URL
        try:
            Key = _NormalizeKey(Key)
            return self._BuildUrl(Key)
        except Exception as e:
            Logger.exception('LocalBackend SignedUrl 失败: key=%s', Key)
            raise RuntimeError(f'本地存储生成 URL 失败: {e}') from e

    def Exists(self, Key):
        try:
            Key = _NormalizeKey(Key)
            FilePath = self.BaseDir / Key
            Resolved = FilePath.resolve()
            if not str(Resolved).startswith(str(self.BaseDir.resolve())):
                return False
            return FilePath.exists() and FilePath.is_file()
        except Exception as e:
            Logger.exception('LocalBackend Exists 失败: key=%s', Key)
            raise RuntimeError(f'本地存储检查失败: {e}') from e

    def _BuildUrl(self, Key):
        BaseUrl = self.BaseUrl
        if not BaseUrl.endswith('/'):
            BaseUrl += '/'
        return BaseUrl + Key


class CosBackend(StorageBackend):
    """腾讯云 COS 存储后端。"""

    def __init__(self):
        try:
            from qcloud_cos import CosConfig, CosS3Client
        except ImportError:
            raise ImproperlyConfigured(
                '腾讯云 COS 后端需要安装 cos-python-sdk-v5: pip install cos-python-sdk-v5'
            )
        self.SecretId = _config_get('storage', 'COS_SECRET_ID')
        self.SecretKey = _config_get('storage', 'COS_SECRET_KEY')
        self.Region = _config_get('storage', 'COS_REGION')
        self.Bucket = _config_get('storage', 'COS_BUCKET')
        self.CdnDomain = _config_get('storage', 'COS_CDN_DOMAIN')
        if not all([self.SecretId, self.SecretKey, self.Region, self.Bucket]):
            raise ImproperlyConfigured(
                'COS 配置不完整，需 COS_SECRET_ID/COS_SECRET_KEY/COS_REGION/COS_BUCKET'
            )
        Config = CosConfig(Region=self.Region, SecretId=self.SecretId, SecretKey=self.SecretKey)
        self.Client = CosS3Client(Config)

    def Upload(self, Key, Data, ContentType=None):
        try:
            Key = _NormalizeKey(Key)
            if hasattr(Data, 'read'):
                Data = Data.read()
            if not isinstance(Data, (bytes, bytearray, memoryview)):
                raise ValueError('Data 必须是 bytes 或 file-like 对象')
            self.Client.put_object(
                Bucket=self.Bucket,
                Body=bytes(Data),
                Key=Key,
                EnableMD5=True,
            )
            return self._BuildUrl(Key)
        except Exception as e:
            Logger.exception('CosBackend Upload 失败: key=%s', Key)
            raise RuntimeError(f'COS 上传失败: {e}') from e

    def Delete(self, Key):
        try:
            Key = _NormalizeKey(Key)
            self.Client.delete_object(Bucket=self.Bucket, Key=Key)
        except Exception as e:
            Logger.exception('CosBackend Delete 失败: key=%s', Key)
            raise RuntimeError(f'COS 删除失败: {e}') from e

    def SignedUrl(self, Key, Expires=3600):
        try:
            Key = _NormalizeKey(Key)
            return self.Client.get_presigned_url(
                Method='GET',
                Bucket=self.Bucket,
                Key=Key,
                Expired=Expires,
            )
        except Exception as e:
            Logger.exception('CosBackend SignedUrl 失败: key=%s', Key)
            raise RuntimeError(f'COS 生成签名 URL 失败: {e}') from e

    def Exists(self, Key):
        try:
            Key = _NormalizeKey(Key)
            return self.Client.object_exists(Bucket=self.Bucket, Key=Key)
        except Exception as e:
            Logger.exception('CosBackend Exists 失败: key=%s', Key)
            raise RuntimeError(f'COS 检查存在失败: {e}') from e

    def _BuildUrl(self, Key):
        if self.CdnDomain:
            return f'{self.CdnDomain.rstrip("/")}/{Key}'
        return f'https://{self.Bucket}.cos.{self.Region}.myqcloud.com/{Key}'


class OssBackend(StorageBackend):
    """阿里云 OSS 存储后端。"""

    def __init__(self):
        try:
            import oss2
        except ImportError:
            raise ImproperlyConfigured(
                '阿里云 OSS 后端需要安装 oss2: pip install oss2'
            )
        self.AccessKey = _config_get('storage', 'OSS_ACCESS_KEY')
        self.AccessSecret = _config_get('storage', 'OSS_ACCESS_SECRET')
        self.Endpoint = _config_get('storage', 'OSS_ENDPOINT')
        self.Bucket = _config_get('storage', 'OSS_BUCKET')
        self.CdnDomain = _config_get('storage', 'OSS_CDN_DOMAIN')
        if not all([self.AccessKey, self.AccessSecret, self.Endpoint, self.Bucket]):
            raise ImproperlyConfigured(
                'OSS 配置不完整，需 OSS_ACCESS_KEY/OSS_ACCESS_SECRET/OSS_ENDPOINT/OSS_BUCKET'
            )
        Auth = oss2.Auth(self.AccessKey, self.AccessSecret)
        self.BucketObj = oss2.Bucket(Auth, self.Endpoint, self.Bucket)

    def Upload(self, Key, Data, ContentType=None):
        try:
            Key = _NormalizeKey(Key)
            if hasattr(Data, 'read'):
                Data = Data.read()
            if not isinstance(Data, (bytes, bytearray, memoryview)):
                raise ValueError('Data 必须是 bytes 或 file-like 对象')
            Headers = {}
            if ContentType:
                Headers['Content-Type'] = ContentType
            self.BucketObj.put_object(Key, bytes(Data), headers=Headers)
            return self._BuildUrl(Key)
        except Exception as e:
            Logger.exception('OssBackend Upload 失败: key=%s', Key)
            raise RuntimeError(f'OSS 上传失败: {e}') from e

    def Delete(self, Key):
        try:
            Key = _NormalizeKey(Key)
            self.BucketObj.delete_object(Key)
        except Exception as e:
            Logger.exception('OssBackend Delete 失败: key=%s', Key)
            raise RuntimeError(f'OSS 删除失败: {e}') from e

    def SignedUrl(self, Key, Expires=3600):
        try:
            Key = _NormalizeKey(Key)
            return self.BucketObj.sign_url('GET', Key, Expires)
        except Exception as e:
            Logger.exception('OssBackend SignedUrl 失败: key=%s', Key)
            raise RuntimeError(f'OSS 生成签名 URL 失败: {e}') from e

    def Exists(self, Key):
        try:
            Key = _NormalizeKey(Key)
            return self.BucketObj.object_exists(Key)
        except Exception as e:
            Logger.exception('OssBackend Exists 失败: key=%s', Key)
            raise RuntimeError(f'OSS 检查存在失败: {e}') from e

    def _BuildUrl(self, Key):
        if self.CdnDomain:
            return f'{self.CdnDomain.rstrip("/")}/{Key}'
        Endpoint = self.Endpoint
        for Scheme in ('https://', 'http://'):
            if Endpoint.startswith(Scheme):
                Endpoint = Endpoint[len(Scheme):]
                break
        return f'https://{self.Bucket}.{Endpoint}/{Key}'


class Storage:
    """统一存储抽象层（单例门面）。

    首次访问时根据 app.ini [storage] BACKEND 初始化对应后端并缓存。
    """

    _Backend = None

    @classmethod
    def _GetBackend(cls):
        if cls._Backend is None:
            BackendName = (_config_get('storage', 'BACKEND') or 'local').lower()
            if BackendName == 'local':
                cls._Backend = LocalBackend()
            elif BackendName == 'cos':
                cls._Backend = CosBackend()
            elif BackendName == 'oss':
                cls._Backend = OssBackend()
            else:
                raise ImproperlyConfigured(f'不支持的存储后端: {BackendName}')
            Logger.info('存储后端已初始化: %s', BackendName)
        return cls._Backend

    @classmethod
    def Upload(cls, Key, Data, ContentType=None):
        """上传 bytes 或 file-like 对象，返回可访问 URL。"""
        return cls._GetBackend().Upload(Key, Data, ContentType)

    @classmethod
    def UploadFile(cls, Key, FileObj, ContentType=None):
        """上传文件对象（有 read 方法），返回可访问 URL。"""
        return cls._GetBackend().Upload(Key, FileObj, ContentType)

    @classmethod
    def Delete(cls, Key):
        """删除指定 key 的对象。"""
        return cls._GetBackend().Delete(Key)

    @classmethod
    def SignedUrl(cls, Key, Expires=3600):
        """生成签名 URL（local 后端直接返回公开 URL）。"""
        return cls._GetBackend().SignedUrl(Key, Expires)

    @classmethod
    def Exists(cls, Key):
        """判断指定 key 是否存在。"""
        return cls._GetBackend().Exists(Key)
