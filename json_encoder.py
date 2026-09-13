"""GVS 自定义 JSON 编码器与渲染器——处理 UUIDObj 等非标准类型的序列化"""

from rest_framework.renderers import JSONRenderer as _DRFJSONRenderer
from rest_framework.utils.encoders import JSONEncoder as _DRFJSONEncoder
from gvsdsdk.model_utils import UUIDObj


class JSONEncoder(_DRFJSONEncoder):
    """扩展 DRF 的 JSONEncoder，支持 UUIDObj 序列化"""

    def default(self, obj):
        if isinstance(obj, UUIDObj):
            return obj.str
        return super().default(obj)


class JSONRenderer(_DRFJSONRenderer):
    """使用自定义 JSONEncoder 的渲染器"""
    encoder_class = JSONEncoder
