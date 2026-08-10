import datetime
from django.db import migrations, models
import django.db.models.deletion
import gvsdsdk.models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name='ServiceNode',
            fields=[
                ('ServiceUUID', gvsdsdk.models.UUIDField(verbose_name='服务UUID', editable=False, primary_key=True, serialize=False, binary_length=16)),
                ('ServiceName', models.CharField(db_column='ServiceName', max_length=128, unique=True)),
                ('ServiceDomain', models.CharField(db_column='ServiceDomain', max_length=256)),
                ('ServiceSecretHash', models.CharField(db_column='ServiceSecretHash', max_length=128)),
                ('ServiceScope', models.JSONField(db_column='ServiceScope', default=list)),
                ('ServiceStatus', models.SmallIntegerField(db_column='ServiceStatus', default=1)),
                ('ServiceType', models.SmallIntegerField(db_column='ServiceType', default=0)),
                ('ServiceMetadata', models.JSONField(db_column='ServiceMetadata', default=dict)),
                ('CreateTime', models.DateTimeField(db_column='CreateTime', default=datetime.datetime.utcnow)),
                ('LastHeartbeat', models.DateTimeField(blank=True, db_column='LastHeartbeat', null=True)),
            ],
            options={
                'verbose_name': '服务节点',
                'verbose_name_plural': '服务节点',
                'db_table': 'ServiceNode',
                'ordering': ['-CreateTime'],
            },
        ),
        migrations.CreateModel(
            name='ServiceToken',
            fields=[
                ('TokenUUID', gvsdsdk.models.UUIDField(verbose_name='令牌UUID', editable=False, primary_key=True, serialize=False, binary_length=16)),
                ('AccessToken', models.CharField(db_column='AccessToken', max_length=512, unique=True)),
                ('RefreshToken', models.CharField(db_column='RefreshToken', max_length=512, unique=True)),
                ('TokenScope', models.JSONField(db_column='TokenScope', default=list)),
                ('ExpiresAt', models.DateTimeField(db_column='ExpiresAt')),
                ('RefreshExpiresAt', models.DateTimeField(db_column='RefreshExpiresAt')),
                ('CreateTime', models.DateTimeField(db_column='CreateTime', default=datetime.datetime.utcnow)),
                ('TokenStatus', models.SmallIntegerField(db_column='TokenStatus', default=1)),
                ('ServiceUUID', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='tokens', to='msync.servicenode')),
            ],
            options={
                'verbose_name': '服务令牌',
                'verbose_name_plural': '服务令牌',
                'db_table': 'ServiceToken',
                'ordering': ['-CreateTime'],
            },
        ),
        migrations.CreateModel(
            name='ObjectGlobalMapping',
            fields=[
                ('OGMUUID', gvsdsdk.models.UUIDField(verbose_name='OGM UUID', editable=False, primary_key=True, serialize=False, binary_length=16)),
                ('ObjectType', models.CharField(db_column='ObjectType', max_length=64)),
                ('ObjectLocalID', models.CharField(db_column='ObjectLocalID', max_length=128)),
                ('ObjectGlobalID', models.CharField(db_column='ObjectGlobalID', max_length=128, unique=True)),
                ('ObjectMetadata', models.JSONField(db_column='ObjectMetadata', default=dict)),
                ('ObjectStatus', models.SmallIntegerField(db_column='ObjectStatus', default=1)),
                ('CreateTime', models.DateTimeField(db_column='CreateTime', default=datetime.datetime.utcnow)),
                ('UpdateTime', models.DateTimeField(blank=True, db_column='UpdateTime', null=True)),
                ('OwnerServiceUUID', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='owned_objects', to='msync.servicenode')),
            ],
            options={
                'verbose_name': '对象全局映射',
                'verbose_name_plural': '对象全局映射',
                'db_table': 'OGM',
                'ordering': ['-CreateTime'],
                'unique_together': {('ObjectType', 'ObjectLocalID', 'OwnerServiceUUID')},
            },
        ),
        migrations.AddIndex(
            model_name='objectglobalmapping',
            index=models.Index(fields=['ObjectType'], name='idx_ogm_type'),
        ),
        migrations.AddIndex(
            model_name='objectglobalmapping',
            index=models.Index(fields=['ObjectGlobalID'], name='idx_ogm_gid'),
        ),
        migrations.CreateModel(
            name='DistributedTransaction',
            fields=[
                ('TransactionUUID', gvsdsdk.models.UUIDField(verbose_name='事务UUID', editable=False, primary_key=True, serialize=False, binary_length=16)),
                ('TransactionCode', models.CharField(db_column='TransactionCode', max_length=64, unique=True)),
                ('TransactionType', models.CharField(choices=[('saga', 'Saga'), ('2pc', '两阶段提交')], db_column='TransactionType', default='saga', max_length=16)),
                ('TransactionName', models.CharField(db_column='TransactionName', max_length=128)),
                ('TransactionDescription', models.CharField(db_column='TransactionDescription', default='', max_length=512)),
                ('TransactionStatus', models.SmallIntegerField(choices=[(0, '待执行'), (1, '执行中'), (2, '已提交'), (3, '补偿中'), (4, '已补偿'), (5, '失败'), (6, '超时')], db_column='TransactionStatus', default=0)),
                ('ReferenceOGMUUID', gvsdsdk.models.UUIDField(verbose_name='关联OGM UUID', blank=True, null=True, binary_length=16)),
                ('TransactionPayload', models.JSONField(db_column='TransactionPayload', default=dict)),
                ('TransactionResult', models.JSONField(db_column='TransactionResult', default=dict)),
                ('TimeoutSeconds', models.IntegerField(db_column='TimeoutSeconds', default=300)),
                ('CreateTime', models.DateTimeField(db_column='CreateTime', default=datetime.datetime.utcnow)),
                ('UpdateTime', models.DateTimeField(blank=True, db_column='UpdateTime', null=True)),
                ('EndTime', models.DateTimeField(blank=True, db_column='EndTime', null=True)),
                ('InitiatorServiceUUID', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='initiated_dtx', to='msync.servicenode')),
            ],
            options={
                'verbose_name': '分布式事务',
                'verbose_name_plural': '分布式事务',
                'db_table': 'DistributedTransaction',
                'ordering': ['-CreateTime'],
            },
        ),
        migrations.AddIndex(
            model_name='distributedtransaction',
            index=models.Index(fields=['TransactionStatus'], name='idx_dtx_status'),
        ),
        migrations.AddIndex(
            model_name='distributedtransaction',
            index=models.Index(fields=['InitiatorServiceUUID'], name='idx_dtx_initiator'),
        ),
        migrations.CreateModel(
            name='TransactionStep',
            fields=[
                ('StepUUID', gvsdsdk.models.UUIDField(verbose_name='步骤UUID', editable=False, primary_key=True, serialize=False, binary_length=16)),
                ('StepIndex', models.IntegerField(db_column='StepIndex')),
                ('StepName', models.CharField(db_column='StepName', max_length=128)),
                ('StepDescription', models.CharField(db_column='StepDescription', default='', max_length=512)),
                ('StepStatus', models.SmallIntegerField(choices=[(0, '待执行'), (1, '执行中'), (2, '已完成'), (3, '补偿中'), (4, '已补偿'), (5, '失败'), (6, '已跳过')], db_column='StepStatus', default=0)),
                ('ActionEndpoint', models.CharField(db_column='ActionEndpoint', max_length=512)),
                ('ActionMethod', models.CharField(db_column='ActionMethod', default='POST', max_length=8)),
                ('ActionPayload', models.JSONField(db_column='ActionPayload', default=dict)),
                ('CompensateEndpoint', models.CharField(db_column='CompensateEndpoint', default='', max_length=512)),
                ('CompensateMethod', models.CharField(db_column='CompensateMethod', default='POST', max_length=8)),
                ('CompensatePayload', models.JSONField(db_column='CompensatePayload', default=dict)),
                ('StepResult', models.JSONField(db_column='StepResult', default=dict)),
                ('RetryCount', models.IntegerField(db_column='RetryCount', default=0)),
                ('MaxRetries', models.IntegerField(db_column='MaxRetries', default=3)),
                ('CreateTime', models.DateTimeField(db_column='CreateTime', default=datetime.datetime.utcnow)),
                ('StartTime', models.DateTimeField(blank=True, db_column='StartTime', null=True)),
                ('EndTime', models.DateTimeField(blank=True, db_column='EndTime', null=True)),
                ('ParticipantServiceUUID', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='participated_steps', to='msync.servicenode')),
                ('TransactionUUID', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='steps', to='msync.distributedtransaction')),
            ],
            options={
                'verbose_name': '事务步骤',
                'verbose_name_plural': '事务步骤',
                'db_table': 'TransactionStep',
                'ordering': ['TransactionUUID', 'StepIndex'],
            },
        ),
        migrations.AddIndex(
            model_name='transactionstep',
            index=models.Index(fields=['TransactionUUID', 'StepIndex'], name='idx_step_tx_order'),
        ),
    ]
