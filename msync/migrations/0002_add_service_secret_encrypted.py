from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('msync', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='servicenode',
            name='ServiceSecretEncrypted',
            field=models.CharField(db_column='ServiceSecretEncrypted', default='', max_length=512),
        ),
    ]
