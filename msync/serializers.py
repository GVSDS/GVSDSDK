from rest_framework import serializers
from gvsdsdk.serializer_base import PascalCaseSerializer, PascalCaseModelSerializer
from gvsdsdk.msync.models import ServiceNode


class ServiceNodeSerializer(PascalCaseModelSerializer):
    class Meta:
        model = ServiceNode
        fields = ['ServiceName', 'ServiceDomain', 'ServiceScope', 'ServiceType', 'ServiceMetadata', 'ServiceStatus', 'CreateTime', 'LastHeartbeat']
        read_only_fields = ['CreateTime', 'LastHeartbeat']


class ServiceTokenResponseSerializer(PascalCaseSerializer):
    AccessToken = serializers.CharField()
    RefreshToken = serializers.CharField()
    Scope = serializers.ListField()
    ExpiresAt = serializers.FloatField()


class AuthTokenRequestSerializer(PascalCaseSerializer):
    ServiceName = serializers.CharField()
    Payload = serializers.DictField()
    Signature = serializers.CharField()


class RefreshTokenRequestSerializer(PascalCaseSerializer):
    RefreshToken = serializers.CharField()


class OGMRegisterSerializer(PascalCaseSerializer):
    ObjectType = serializers.CharField()
    ObjectLocalID = serializers.CharField()
    ServiceName = serializers.CharField()
    ObjectGlobalID = serializers.CharField(required=False, allow_blank=True)
    Metadata = serializers.DictField(required=False, default=dict)


class OGMBatchRegisterSerializer(PascalCaseSerializer):
    Items = OGMRegisterSerializer(many=True)
    ServiceName = serializers.CharField()


class ServiceRegisterSerializer(PascalCaseSerializer):
    ServiceName = serializers.CharField()
    ServiceDomain = serializers.CharField()
    ServiceSecret = serializers.CharField(write_only=True)
    ServiceSecretHash = serializers.CharField(required=False, allow_blank=True)
    ServiceScope = serializers.ListField(required=False, default=['ogm:read', 'ogm:write', 'registry:read'])
    ServiceType = serializers.IntegerField(default=0)
    ServiceMetadata = serializers.DictField(required=False, default=dict)


class TransactionStepSerializer(PascalCaseSerializer):
    StepName = serializers.CharField()
    StepDescription = serializers.CharField(required=False, default='')
    ParticipantServiceUUID = serializers.CharField(required=False, allow_blank=True)
    ActionEndpoint = serializers.CharField()
    ActionMethod = serializers.CharField(default='POST')
    ActionPayload = serializers.DictField(required=False, default=dict)
    CompensateEndpoint = serializers.CharField(required=False, default='')
    CompensateMethod = serializers.CharField(default='POST')
    CompensatePayload = serializers.DictField(required=False, default=dict)
    MaxRetries = serializers.IntegerField(default=3)


class TransactionCreateSerializer(PascalCaseSerializer):
    TransactionName = serializers.CharField()
    TransactionDescription = serializers.CharField(required=False, default='')
    TransactionType = serializers.ChoiceField(choices=['saga', '2pc'], default='saga')
    Steps = TransactionStepSerializer(many=True)
    InitiatorServiceName = serializers.CharField(required=False, allow_blank=True)
    ReferenceOGMUUID = serializers.CharField(required=False, allow_blank=True)
    Payload = serializers.DictField(required=False, default=dict)
    TimeoutSeconds = serializers.IntegerField(default=300)


class TransactionExecuteSerializer(PascalCaseSerializer):
    AccessToken = serializers.CharField(required=False, allow_blank=True)


class TransactionRetryStepSerializer(PascalCaseSerializer):
    StepIndex = serializers.IntegerField()
    AccessToken = serializers.CharField(required=False, allow_blank=True)


class LocalExecuteSerializer(PascalCaseSerializer):
    StepName = serializers.CharField()
    Payload = serializers.DictField(required=False, default=dict)
    TransactionCode = serializers.CharField(required=False, allow_blank=True)
    StepIndex = serializers.IntegerField(required=False, allow_null=True)


class LocalCompensateSerializer(PascalCaseSerializer):
    StepName = serializers.CharField()
    Payload = serializers.DictField(required=False, default=dict)
    ActionResult = serializers.DictField(required=False, default=dict)
    TransactionCode = serializers.CharField(required=False, allow_blank=True)
    StepIndex = serializers.IntegerField(required=False, allow_null=True)
