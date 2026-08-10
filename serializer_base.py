"""GVSDS SDK DRF 序列化器基类

提供 PascalCase 命名风格的 DRF 序列化器基类（is_valid/validated_data 的
PascalCase 别名）。gvsds.common.Serializer 从本模块 re-export。"""

from rest_framework import serializers


class PascalCaseSerializer(serializers.Serializer):

    def IsValid(self, raise_exception=False):
        return self.is_valid(raise_exception=raise_exception)

    @property
    def ValidatedData(self):
        return self.validated_data


class PascalCaseModelSerializer(serializers.ModelSerializer):

    def IsValid(self, raise_exception=False):
        return self.is_valid(raise_exception=raise_exception)

    @property
    def ValidatedData(self):
        return self.validated_data
