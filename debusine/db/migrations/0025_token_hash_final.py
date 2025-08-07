import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [("db", "0024_token_hash_data")]
    replaces = [("db", "0023_token_hash")]

    operations = [
        migrations.AlterField(
            model_name='token',
            name='hash',
            field=models.CharField(
                unique=True,
                max_length=64,
                validators=[
                    django.core.validators.MaxLengthValidator(64),
                    django.core.validators.MinLengthValidator(64),
                ],
                verbose_name='Hexadecimal hash, length is 64 chars',
            ),
        ),
        # We add null=True so that we can 'attempt' to reverse the data
        # migration, which will fail anyway in revert_hash if there are
        # tokens present.
        migrations.AlterField(
            model_name='token',
            name='key',
            field=models.CharField(
                unique=True,
                null=True,
                default='',
                max_length=64,
                validators=[
                    django.core.validators.MaxLengthValidator(64),
                    django.core.validators.MinLengthValidator(64),
                ],
                verbose_name='Hexadecimal key, length is 64 chars',
            ),
        ),
        migrations.RemoveField(
            model_name='token',
            name='key',
        ),
    ]
