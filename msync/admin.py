from django.contrib import admin
from gvsdsdk.msync.models import (
    ServiceNode,
    ServiceToken,
    ObjectGlobalMapping,
    DistributedTransaction,
    TransactionStep,
)


@admin.register(ServiceNode)
class ServiceNodeAdmin(admin.ModelAdmin):
    list_display = ('ServiceName', 'ServiceDomain', 'ServiceType', 'ServiceStatus', 'CreateTime', 'LastHeartbeat')
    list_filter = ('ServiceType', 'ServiceStatus')
    search_fields = ('ServiceName', 'ServiceDomain')
    readonly_fields = ('ServiceUUID', 'ServiceSecretHash', 'CreateTime')


@admin.register(ServiceToken)
class ServiceTokenAdmin(admin.ModelAdmin):
    list_display = ('TokenUUID', 'ServiceUUID', 'ExpiresAt', 'TokenStatus', 'CreateTime')
    list_filter = ('TokenStatus',)
    readonly_fields = ('TokenUUID', 'AccessToken', 'RefreshToken', 'CreateTime')


@admin.register(ObjectGlobalMapping)
class ObjectGlobalMappingAdmin(admin.ModelAdmin):
    list_display = ('ObjectGlobalID', 'ObjectType', 'ObjectLocalID', 'OwnerServiceUUID', 'ObjectStatus', 'CreateTime')
    list_filter = ('ObjectType', 'ObjectStatus')
    search_fields = ('ObjectGlobalID', 'ObjectLocalID')


@admin.register(DistributedTransaction)
class DistributedTransactionAdmin(admin.ModelAdmin):
    list_display = ('TransactionCode', 'TransactionName', 'TransactionType', 'TransactionStatus', 'InitiatorServiceUUID', 'CreateTime', 'EndTime')
    list_filter = ('TransactionType', 'TransactionStatus')
    search_fields = ('TransactionCode', 'TransactionName')
    readonly_fields = ('TransactionUUID', 'CreateTime')


@admin.register(TransactionStep)
class TransactionStepAdmin(admin.ModelAdmin):
    list_display = ('StepUUID', 'TransactionUUID', 'StepIndex', 'StepName', 'StepStatus', 'ParticipantServiceUUID', 'RetryCount')
    list_filter = ('StepStatus',)
    readonly_fields = ('StepUUID', 'CreateTime')
