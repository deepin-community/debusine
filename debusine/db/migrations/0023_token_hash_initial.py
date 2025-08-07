import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        (
            "db",
            "0022_collectionitem_db_collectionitem_unique_debian_environment",
        ),
    ]
    replaces = [("db", "0023_token_hash")]

    operations = [
        migrations.AddField(
            model_name='token',
            name='hash',
            field=models.CharField(
                default='',
                max_length=64,
                validators=[
                    django.core.validators.MaxLengthValidator(64),
                    django.core.validators.MinLengthValidator(64),
                ],
                verbose_name='Hexadecimal hash, length is 64 chars',
            ),
        ),
    ]
