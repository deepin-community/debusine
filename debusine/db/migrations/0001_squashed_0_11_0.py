# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

import datetime

from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.contrib.auth.validators import UnicodeUsernameValidator
from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.operations import BtreeGistExtension
from django.core.serializers.json import DjangoJSONEncoder
from django.core.validators import MaxLengthValidator, MinLengthValidator
from django.db import migrations, models
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.state import StateApps
from django.db.models.expressions import CombinedExpression
from django.db.models.fields.json import KeyTextTransform
from django.utils import timezone
from pgtrigger.compiler import Trigger, UpsertTriggerSql

from debusine.artifacts.models import (
    ArtifactCategory,
    CollectionCategory,
    TaskTypes,
)
from debusine.assets.models import AssetCategory
from debusine.db.constraints import JsonDataUniqueConstraint
from debusine.db.models import DEFAULT_FILE_STORE_NAME, SYSTEM_USER_NAME
from debusine.db.models.auth import UserManager, validate_group_name
from debusine.db.models.collections import validate_collection_name
from debusine.db.models.files import _FileStoreBackendChoices
from debusine.db.models.scopes import validate_scope_name
from debusine.db.models.work_requests import validate_notification_channel_name
from debusine.db.models.workspaces import validate_workspace_name
from debusine.tasks.models import WorkerType

# We only create the collections that were defined as singletons at the time
# of this migration.  If we add new singleton categories and want to create
# them in the default workspace as well, then that requires a new migration.
singleton_collection_categories = (
    CollectionCategory.PACKAGE_BUILD_LOGS,
    CollectionCategory.TASK_HISTORY,
)


def create_default_objects(
    apps: StateApps, schema_editor: BaseDatabaseSchemaEditor
) -> None:
    Collection = apps.get_model("db", "Collection")
    FileStore = apps.get_model("db", "FileStore")
    Scope = apps.get_model("db", "Scope")
    Workspace = apps.get_model("db", "Workspace")
    User = apps.get_model("db", "User")

    default_file_store = FileStore.objects.create(
        name=DEFAULT_FILE_STORE_NAME,
        backend=_FileStoreBackendChoices.LOCAL,
        configuration={},
    )
    default_scope = Scope.objects.create(
        name=settings.DEBUSINE_DEFAULT_SCOPE,
        label=settings.DEBUSINE_DEFAULT_SCOPE.capitalize(),
    )
    default_scope.file_stores.set(
        [default_file_store],
        through_defaults={"upload_priority": 100, "download_priority": 100},
    )
    default_workspace = Workspace.objects.create(
        scope=default_scope,
        name=settings.DEBUSINE_DEFAULT_WORKSPACE,
        public=True,
    )

    unusable_password = make_password(None)
    User.objects.create(
        username=SYSTEM_USER_NAME,
        password=unusable_password,
        first_name="Debusine",
        last_name="System",
        is_system=True,
    )

    for category in singleton_collection_categories:
        Collection.objects.create(
            name="_", category=category, workspace=default_workspace
        )
    Collection.objects.create(
        name="default",
        category=CollectionCategory.TASK_CONFIGURATION,
        workspace=default_workspace,
    )


class Migration(migrations.Migration):
    replaces = [
        ("db", "0001_initial"),
        ("db", "0002_create_default_store"),
        ("db", "0003_create_workspace"),
        ("db", "0004_add_field_user_to_token"),
        ("db", "0005_workrequest_created_by"),
        ("db", "0006_alter_workrequest_created_by"),
        ("db", "0007_notificationchannel"),
        ("db", "0008_artifact_created_by"),
        ("db", "0009_alter_token_comment"),
        ("db", "0010_identity"),
        ("db", "0011_auto_20231024_1213"),
        ("db", "0012_workspace_default_expiration_delay"),
        ("db", "0013_expiration_delay"),
        ("db", "0014_artifact_underscore_names"),
        ("db", "0015_alter_fileinartifact_file_non_null"),
        ("db", "0016_worker_internal"),
        ("db", "0017_collections"),
        ("db", "0018_workrequest_worker_blank"),
        ("db", "0019_autopkgtest_task_data"),
        ("db", "0020_alter_filestore_backend"),
        ("db", "0021_rename_constraints"),
        (
            "db",
            "0022_collectionitem_db_collectionitem_unique_debian_environment",
        ),
        ("db", "0023_token_hash_initial"),
        ("db", "0024_token_hash_data"),
        ("db", "0025_token_hash_final"),
        ("db", "0026_suite_collection"),
        ("db", "0027_workrequest_task_type"),
        ("db", "0028_workrequest_changes_for_workflows"),
        ("db", "0029_workflowtemplate"),
        ("db", "0030_move_task_type"),
        ("db", "0031_workrequest_priorities"),
        ("db", "0032_workrequest_manage_priorities_permission"),
        ("db", "0033_collectionitem_tighten_unique_active_name"),
        ("db", "0034_workrequest_pydantic"),
        ("db", "0035_autopkgtest_extra_environment"),
        ("db", "0036_workrequest_fix_task_type"),
        ("db", "0037_workrequest_internal_collection"),
        ("db", "0038_alter_workrequest_parent"),
        ("db", "0039_rename_environment_id"),
        ("db", "0040_blhc_lookup"),
        ("db", "0041_piuparts_lookup"),
        ("db", "0042_user_is_system"),
        ("db", "0043_create_system_user"),
        ("db", "0044_collection_add_retains_artifacts"),
        ("db", "0045_autopkgtest_lookup"),
        ("db", "0046_lintian_lookup"),
        ("db", "0047_sbuild_lookup"),
        ("db", "0048_alter_collection_retains_artifacts"),
        ("db", "0049_update_suite_lintian_collection_lookup"),
        ("db", "0050_collection_workflow_initial"),
        ("db", "0051_collection_workflow_data"),
        ("db", "0052_collection_workflow_final"),
        ("db", "0053_debian_environments_typo"),
        ("db", "0054_alter_workrequest_options"),
        ("db", "0055_artifact_created_by_work_request_set_null"),
        ("db", "0056_debian_environments_backend"),
        ("db", "0057_workrequest_expiration_delay"),
        ("db", "0058_workrequest_dynamic_task_data"),
        ("db", "0059_collection_workspace_namespace"),
        ("db", "0060_workspace_chain"),
        ("db", "0061_workrequest_supersedes"),
        ("db", "0062_rename_internalnoop"),
        ("db", "0063_worker_worker_type_initial"),
        ("db", "0064_worker_worker_type_data"),
        ("db", "0065_worker_worker_type_final"),
        ("db", "0066_signing_worker_type"),
        ("db", "0067_alter_filestore_backend"),
        ("db", "0068_collection_workflow_onetoone"),
        ("db", "0069_collectionitem_parent_category_initial"),
        ("db", "0070_collectionitem_parent_category_data"),
        ("db", "0071_collectionitem_parent_category_final"),
        ("db", "0072_worker_last_seen_at"),
        ("db", "0073_token_last_seen_at"),
        ("db", "0074_fileinartifact_complete_initial"),
        ("db", "0075_fileinartifact_complete_data"),
        ("db", "0076_fileinartifact_complete_final"),
        ("db", "0077_wait_task_type"),
        ("db", "0078_collectionitem_add_created_removed_by_workflow"),
        ("db", "0079_sbuild_binnmu_build_components"),
        ("db", "0080_scope"),
        ("db", "0081_create_fallback_scope"),
        ("db", "0082_workspace_add_scope"),
        ("db", "0083_set_fallback_scope"),
        ("db", "0084_workspace_namespace_by_scope"),
        ("db", "0085_scope_name_validation"),
        ("db", "0086_add_scoped_group"),
        ("db", "0087_django_json_encoder"),
        ("db", "0088_roles_for_scopes"),
        ("db", "0089_group_name_validation"),
        ("db", "0090_workspacerole_and_more"),
        ("db", "0091_sign_multiple_unsigned"),
        ("db", "0092_default_workspace_public"),
        ("db", "0093_uniform_scoperole_resource_reference"),
        ("db", "0094_collection_db_collection_name_not_reserved"),
        ("db", "0095_create_singleton_collections"),
        ("db", "0096_workspace_name_validation"),
        ("db", "0097_recreate_json_data_unique_constraint"),
        ("db", "0098_artifact_original_artifact"),
        ("db", "0099_delete_dynamic_data_workflows"),
        ("db", "0100_name_validation"),
        ("db", "0101_collectionitem_suite_indexes"),
        ("db", "0102_collection_related_name"),
        ("db", "0103_cascade_deletion_on_role_assignments"),
        ("db", "0104_workspacerole_contributor"),
        ("db", "0105_wait_needs_input"),
        ("db", "0106_workrequest_workflow_template"),
        ("db", "0107_add_scope_label_icon"),
        ("db", "0108_fill_scope_label"),
        ("db", "0109_scope_label_required_and_unique"),
        ("db", "0110_workrequest_workflow_last_activity_at"),
        ("db", "0111_workrequest_workflow_runtime_status"),
        ("db", "0112_workrequest_autopkgtest_extra_repositories"),
        ("db", "0113_workrequest_output_data_json"),
        ("db", "0114_user_manager_with_permissions"),
        ("db", "0115_workrequest_workflow_runtime_status_use_value"),
        ("db", "0116_assets"),
        ("db", "0117_signing_work_request_fingerprints"),
        ("db", "0118_signing_key_assets"),
        ("db", "0119_scope_file_stores_initial"),
        ("db", "0120_scope_file_stores_data"),
        ("db", "0121_remove_workspace_file_stores"),
        ("db", "0122_create_default_task_configuration_collections"),
        ("db", "0123_asset_cloud_provider_account"),
        (
            "db",
            "0124_drop_category_from_db_asset_unique_signing_key_fingerprints",
        ),
        ("db", "0125_add_group_ephemeral"),
        ("db", "0126_filestore_instance_wide"),
        ("db", "0127_workspace_add_expiry_fields"),
        ("db", "0128_filestore_add_size_columns"),
        ("db", "0129_filestore_total_size"),
        ("db", "0130_filestore_total_size_triggers"),
        ("db", "0131_workrequest_add_configured_task_data_and_version"),
        ("db", "0132_filestore_size_columns_biginteger"),
        ("db", "0133_filestore_provider_account_alter_filestore_backend"),
        ("db", "0134_filestoreinscope_db_filestoreinscope_consistent_populate"),
        ("db", "0135_workerpools"),
        ("db", "0136_add_groupmembership"),
        ("db", "0137_add_groupmembership_role"),
        ("db", "0138_worker_activation_token"),
        ("db", "0139_groupmembership_unique_role"),
        ("db", "0140_groupauditlog"),
        ("db", "0141_add_clientenroll"),
        ("db", "0142_workrequest_workflows_need_update"),
    ]

    initial = True

    dependencies = [("auth", "0012_alter_user_first_name_max_length")]

    operations = [
        BtreeGistExtension(),
        migrations.CreateModel(
            name="User",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "password",
                    models.CharField(max_length=128, verbose_name="password"),
                ),
                (
                    "last_login",
                    models.DateTimeField(
                        blank=True, null=True, verbose_name="last login"
                    ),
                ),
                (
                    "is_superuser",
                    models.BooleanField(
                        default=False,
                        help_text="Designates that this user has all permissions without explicitly assigning them.",
                        verbose_name="superuser status",
                    ),
                ),
                (
                    "groups",
                    models.ManyToManyField(
                        blank=True,
                        help_text="The groups this user belongs to. A user will get all permissions granted to each of their groups.",
                        related_name="user_set",
                        related_query_name="user",
                        to="auth.group",
                        verbose_name="groups",
                    ),
                ),
                (
                    "user_permissions",
                    models.ManyToManyField(
                        blank=True,
                        help_text="Specific permissions for this user.",
                        related_name="user_set",
                        related_query_name="user",
                        to="auth.permission",
                        verbose_name="user permissions",
                    ),
                ),
                (
                    "username",
                    models.CharField(
                        error_messages={
                            "unique": "A user with that username already exists."
                        },
                        help_text="Required. 150 characters or fewer. Letters, digits and @/./+/-/_ only.",
                        max_length=150,
                        unique=True,
                        validators=[UnicodeUsernameValidator()],
                        verbose_name="username",
                    ),
                ),
                (
                    "first_name",
                    models.CharField(
                        blank=True, max_length=150, verbose_name="first name"
                    ),
                ),
                (
                    "last_name",
                    models.CharField(
                        blank=True, max_length=150, verbose_name="last name"
                    ),
                ),
                (
                    "email",
                    models.EmailField(
                        blank=True, max_length=254, verbose_name="email address"
                    ),
                ),
                (
                    "is_staff",
                    models.BooleanField(
                        default=False,
                        help_text="Designates whether the user can log into this admin site.",
                        verbose_name="staff status",
                    ),
                ),
                (
                    "is_active",
                    models.BooleanField(
                        default=True,
                        help_text="Designates whether this user should be treated as active. Unselect this instead of deleting accounts.",
                        verbose_name="active",
                    ),
                ),
                (
                    "date_joined",
                    models.DateTimeField(
                        default=timezone.now, verbose_name="date joined"
                    ),
                ),
                ("is_system", models.BooleanField(default=False)),
            ],
            options={
                "verbose_name": "user",
                "verbose_name_plural": "users",
                "abstract": False,
                "constraints": [
                    models.CheckConstraint(
                        check=models.Q(
                            ("is_system", False), ("email", ""), _connector="OR"
                        ),
                        name="db_user_non_system_email",
                    ),
                    models.UniqueConstraint(
                        condition=models.Q(("is_system", False)),
                        fields=("email",),
                        name="db_user_unique_email",
                    ),
                ],
            },
            managers=[("objects", UserManager())],
        ),
        migrations.CreateModel(
            name="ClientEnroll",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("nonce", models.CharField(max_length=64, unique=True)),
                ("payload", models.JSONField(default=dict)),
            ],
        ),
        migrations.CreateModel(
            name="File",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "sha256",
                    models.BinaryField(
                        help_text="sha256 of the file", max_length=4
                    ),
                ),
                (
                    "size",
                    models.PositiveBigIntegerField(
                        help_text="Size in bytes of the file"
                    ),
                ),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        fields=("sha256", "size"),
                        name="db_file_unique_sha256_size",
                    ),
                    models.CheckConstraint(
                        check=models.Q(("sha256", b""), _negated=True),
                        name="db_file_sha256_not_empty",
                    ),
                ]
            },
        ),
        migrations.CreateModel(
            name="FileStore",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("name", models.CharField(max_length=255, unique=True)),
                (
                    "backend",
                    models.CharField(
                        choices=[
                            ("Local", "Local"),
                            ("ExternalDebianSuite", "ExternalDebianSuite"),
                            ("Memory", "Memory"),
                            ("S3", "S3"),
                        ],
                        max_length=255,
                    ),
                ),
                ("configuration", models.JSONField(blank=True, default=dict)),
                ("instance_wide", models.BooleanField(default=True)),
                (
                    "total_size",
                    models.BigIntegerField(default=0, editable=False),
                ),
                (
                    "soft_max_size",
                    models.BigIntegerField(blank=True, null=True),
                ),
                ("max_size", models.BigIntegerField(blank=True, null=True)),
            ],
            options={
                "triggers": [
                    Trigger(
                        name="db_filestore_sync_instance_wide",
                        sql=UpsertTriggerSql(
                            func="UPDATE db_filestoreinscope SET file_store_instance_wide = NEW.instance_wide WHERE db_filestoreinscope.file_store_id = NEW.id AND db_filestoreinscope.file_store_instance_wide != NEW.instance_wide; RETURN NEW;",
                            hash="7dfd74c5dbb675f55873e1fa249285a172869cf3",
                            operation="INSERT OR UPDATE",
                            pgid="pgtrigger_db_filestore_sync_instance_wide_e2fab",
                            table="db_filestore",
                            when="AFTER",
                        ),
                    )
                ],
                "constraints": [
                    models.CheckConstraint(
                        check=models.Q(
                            ("soft_max_size__gt", models.F("max_size")),
                            _negated=True,
                        ),
                        name="db_filestore_consistent_max_sizes",
                    )
                ],
            },
        ),
        migrations.CreateModel(
            name="Scope",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "name",
                    models.CharField(
                        help_text="internal name for the scope",
                        max_length=255,
                        unique=True,
                        validators=[validate_scope_name],
                    ),
                ),
                (
                    "label",
                    models.CharField(
                        help_text="User-visible name for the scope",
                        max_length=255,
                        unique=True,
                    ),
                ),
                (
                    "icon",
                    models.CharField(
                        blank=True,
                        default="",
                        help_text="Optional user-visible icon, resolved via ``{% static %}`` in templates",
                        max_length=255,
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="FileStoreInScope",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "scope",
                    models.ForeignKey(on_delete=models.PROTECT, to="db.scope"),
                ),
                (
                    "file_store",
                    models.ForeignKey(
                        on_delete=models.PROTECT, to="db.filestore"
                    ),
                ),
                (
                    "_file_store_instance_wide",
                    models.BooleanField(
                        db_column="file_store_instance_wide",
                        default=True,
                        editable=False,
                        help_text="Synced from FileStore.instance_wide; do not update directly",
                    ),
                ),
                ("upload_priority", models.IntegerField(blank=True, null=True)),
                (
                    "download_priority",
                    models.IntegerField(blank=True, null=True),
                ),
                ("drain", models.BooleanField(default=False)),
                ("drain_to", models.TextField(blank=True, null=True)),
                ("populate", models.BooleanField(default=False)),
                ("read_only", models.BooleanField(default=False)),
                ("soft_max_size", models.IntegerField(blank=True, null=True)),
                ("write_only", models.BooleanField(default=False)),
            ],
            options={
                "triggers": [
                    Trigger(
                        name="db_filestoreinscope_sync_instance_wide",
                        sql=UpsertTriggerSql(
                            func="NEW.file_store_instance_wide = (SELECT db_filestore.instance_wide FROM db_filestore WHERE db_filestore.id = NEW.file_store_id); RETURN NEW;",
                            hash="04b1ed9053b9e9ea2cf78d10850742c925eeae66",
                            operation="INSERT OR UPDATE",
                            pgid="pgtrigger_db_filestoreinscope_sync_instance_wide_c82e6",
                            table="db_filestoreinscope",
                            when="BEFORE",
                        ),
                    )
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("scope", "file_store"),
                        name="db_filestoreinscope_unique_scope_file_store",
                    ),
                    models.UniqueConstraint(
                        condition=models.Q(
                            ("_file_store_instance_wide", False)
                        ),
                        fields=("file_store",),
                        name="db_filestoreinscope_unique_file_store_not_instance_wide",
                    ),
                    models.CheckConstraint(
                        check=models.Q(
                            ("populate", False),
                            models.Q(("drain", False), ("read_only", False)),
                            _connector="OR",
                        ),
                        name="db_filestoreinscope_consistent_populate",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="Group",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "name",
                    models.CharField(
                        max_length=255, validators=[validate_group_name]
                    ),
                ),
                (
                    "scope",
                    models.ForeignKey(on_delete=models.PROTECT, to="db.scope"),
                ),
                (
                    "ephemeral",
                    models.BooleanField(
                        default=False,
                        help_text="remove the group if it has no roles assigned",
                    ),
                ),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        fields=("name", "scope"),
                        name="db_group_unique_name_scope",
                    )
                ]
            },
        ),
        migrations.CreateModel(
            name="GroupAuditLog",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("changes", models.JSONField()),
                (
                    "actor",
                    models.ForeignKey(
                        null=True,
                        on_delete=models.SET_NULL,
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "group",
                    models.ForeignKey(
                        on_delete=models.CASCADE,
                        related_name="audit_log",
                        to="db.group",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="GroupMembership",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "group",
                    models.ForeignKey(
                        on_delete=models.CASCADE,
                        related_name="membership",
                        to="db.group",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=models.CASCADE,
                        related_name="group_memberships",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "role",
                    models.CharField(
                        choices=[("admin", "Admin"), ("member", "Member")],
                        default="member",
                        max_length=16,
                    ),
                ),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        fields=("group", "user"),
                        name="db_groupmembership_unique_group_user",
                    )
                ]
            },
        ),
        migrations.CreateModel(
            name="ScopeRole",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "resource",
                    models.ForeignKey(
                        on_delete=models.CASCADE,
                        related_name="roles",
                        to="db.scope",
                    ),
                ),
                (
                    "group",
                    models.ForeignKey(
                        on_delete=models.CASCADE,
                        related_name="scope_roles",
                        to="db.group",
                    ),
                ),
                (
                    "role",
                    models.CharField(
                        choices=[("owner", "Owner")], max_length=16
                    ),
                ),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        fields=("resource", "group", "role"),
                        name="db_scoperole_unique_resource_group_role",
                    )
                ]
            },
        ),
        migrations.CreateModel(
            name="Workspace",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "name",
                    models.CharField(
                        max_length=255, validators=[validate_workspace_name]
                    ),
                ),
                ("public", models.BooleanField(default=False)),
                (
                    "default_expiration_delay",
                    models.DurationField(
                        default=datetime.timedelta(0),
                        help_text="minimal time that a new artifact is kept in the workspace before being expired",
                    ),
                ),
                (
                    "scope",
                    models.ForeignKey(
                        on_delete=models.PROTECT,
                        related_name="workspaces",
                        to="db.scope",
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(
                        auto_now_add=True,
                        help_text="time the workspace was created",
                    ),
                ),
                (
                    "expiration_delay",
                    models.DurationField(
                        blank=True,
                        help_text="if set, time since the last task completion timeafter which the workspace can be deleted",
                        null=True,
                    ),
                ),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        fields=("scope", "name"),
                        name="db_workspace_unique_scope_name",
                    )
                ]
            },
        ),
        migrations.CreateModel(
            name="WorkspaceChain",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "child",
                    models.ForeignKey(
                        help_text="Workspace that falls back on `parent` for lookups",
                        on_delete=models.CASCADE,
                        related_name="chain_parents",
                        to="db.workspace",
                    ),
                ),
                (
                    "parent",
                    models.ForeignKey(
                        help_text="Workspace to be looked up if lookup in `child` fails",
                        on_delete=models.CASCADE,
                        related_name="chain_children",
                        to="db.workspace",
                    ),
                ),
                (
                    "order",
                    models.IntegerField(
                        help_text="Lookup order of this element in the chain"
                    ),
                ),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        fields=("child", "parent"),
                        name="db_workspacechain_unique_child_parent",
                    ),
                    models.UniqueConstraint(
                        fields=("child", "order"),
                        name="db_workspacechain_unique_child_order",
                    ),
                ]
            },
        ),
        migrations.CreateModel(
            name="WorkspaceRole",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "resource",
                    models.ForeignKey(
                        on_delete=models.CASCADE,
                        related_name="roles",
                        to="db.workspace",
                    ),
                ),
                (
                    "group",
                    models.ForeignKey(
                        on_delete=models.CASCADE,
                        related_name="workspace_roles",
                        to="db.group",
                    ),
                ),
                (
                    "role",
                    models.CharField(
                        choices=[
                            ("owner", "Owner"),
                            ("contributor", "Contributor"),
                        ],
                        max_length=16,
                    ),
                ),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        fields=("resource", "group", "role"),
                        name="db_workspacerole_unique_resource_group_role",
                    )
                ]
            },
        ),
        migrations.CreateModel(
            name="Asset",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "category",
                    models.CharField(
                        choices=[
                            (
                                "debusine:cloud-provider-account",
                                "debusine:cloud-provider-account",
                            ),
                            ("debusine:signing-key", "debusine:signing-key"),
                        ],
                        max_length=255,
                    ),
                ),
                (
                    "workspace",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=models.PROTECT,
                        to="db.workspace",
                    ),
                ),
                ("data", models.JSONField(default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=models.PROTECT,
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "constraints": [
                    models.CheckConstraint(
                        check=models.Q(
                            models.Q(
                                ("category", AssetCategory["SIGNING_KEY"]),
                                _negated=True,
                            ),
                            ("workspace__isnull", False),
                            _connector="OR",
                        ),
                        name="db_asset_workspace_not_null",
                    )
                ]
            },
        ),
        migrations.CreateModel(
            name="AssetUsage",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "asset",
                    models.ForeignKey(
                        on_delete=models.CASCADE,
                        related_name="usage",
                        to="db.asset",
                    ),
                ),
                (
                    "workspace",
                    models.ForeignKey(
                        on_delete=models.CASCADE,
                        related_name="asset_usage",
                        to="db.workspace",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="AssetUsageRole",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "role",
                    models.CharField(
                        choices=[("signer", "Signer")], max_length=16
                    ),
                ),
                (
                    "group",
                    models.ForeignKey(
                        on_delete=models.CASCADE,
                        related_name="asset_usage_roles",
                        to="db.group",
                    ),
                ),
                (
                    "resource",
                    models.ForeignKey(
                        on_delete=models.CASCADE,
                        related_name="roles",
                        to="db.assetusage",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="AssetRole",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "role",
                    models.CharField(
                        choices=[("owner", "Owner")], max_length=16
                    ),
                ),
                (
                    "group",
                    models.ForeignKey(
                        on_delete=models.CASCADE,
                        related_name="asset_roles",
                        to="db.group",
                    ),
                ),
                (
                    "resource",
                    models.ForeignKey(
                        on_delete=models.CASCADE,
                        related_name="roles",
                        to="db.asset",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="Artifact",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("category", models.CharField(max_length=255)),
                (
                    "workspace",
                    models.ForeignKey(
                        on_delete=models.PROTECT, to="db.workspace"
                    ),
                ),
                ("data", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "expiration_delay",
                    models.DurationField(blank=True, null=True),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=models.PROTECT,
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "original_artifact",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=models.SET_NULL,
                        to="db.artifact",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="ArtifactRelation",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "artifact",
                    models.ForeignKey(
                        on_delete=models.PROTECT,
                        related_name="relations",
                        to="db.artifact",
                    ),
                ),
                (
                    "target",
                    models.ForeignKey(
                        on_delete=models.PROTECT,
                        related_name="targeted_by",
                        to="db.artifact",
                    ),
                ),
                (
                    "type",
                    models.CharField(
                        choices=[
                            ("extends", "Extends"),
                            ("relates-to", "Relates to"),
                            ("built-using", "Built using"),
                        ],
                        max_length=11,
                    ),
                ),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        fields=("artifact", "target", "type"),
                        name="db_artifactrelation_unique_artifact_target_type",
                    )
                ]
            },
        ),
        migrations.CreateModel(
            name="Collection",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "name",
                    models.CharField(
                        max_length=255, validators=[validate_collection_name]
                    ),
                ),
                ("category", models.CharField(max_length=255)),
                (
                    "full_history_retention_period",
                    models.DurationField(blank=True, null=True),
                ),
                (
                    "metadata_only_retention_period",
                    models.DurationField(blank=True, null=True),
                ),
                (
                    "workspace",
                    models.ForeignKey(
                        on_delete=models.PROTECT,
                        related_name="collections",
                        to="db.workspace",
                    ),
                ),
                (
                    "retains_artifacts",
                    models.CharField(
                        choices=[
                            ("never", "Never"),
                            ("workflow", "While workflow is running"),
                            ("always", "Always"),
                        ],
                        default="always",
                        max_length=8,
                    ),
                ),
                ("data", models.JSONField(blank=True, default=dict)),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        fields=("name", "category", "workspace"),
                        name="db_collection_unique_name_category_workspace",
                    ),
                    models.CheckConstraint(
                        check=models.Q(("name", ""), _negated=True),
                        name="db_collection_name_not_empty",
                    ),
                    models.CheckConstraint(
                        check=models.Q(
                            models.Q(
                                models.Q(
                                    (
                                        "category__in",
                                        {
                                            CollectionCategory[
                                                "PACKAGE_BUILD_LOGS"
                                            ],
                                            CollectionCategory["TASK_HISTORY"],
                                        },
                                    ),
                                    _negated=True,
                                ),
                                models.Q(
                                    ("name__startswith", "_"), _negated=True
                                ),
                            ),
                            models.Q(
                                (
                                    "category__in",
                                    {
                                        CollectionCategory[
                                            "PACKAGE_BUILD_LOGS"
                                        ],
                                        CollectionCategory["TASK_HISTORY"],
                                    },
                                ),
                                ("name", "_"),
                            ),
                            _connector="OR",
                        ),
                        name="db_collection_name_not_reserved",
                    ),
                    models.CheckConstraint(
                        check=models.Q(("category", ""), _negated=True),
                        name="db_collection_category_not_empty",
                    ),
                ]
            },
        ),
        migrations.CreateModel(
            name="CollectionItem",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("name", models.CharField(max_length=255)),
                (
                    "child_type",
                    models.CharField(
                        choices=[
                            ("b", "Bare"),
                            ("a", "Artifact"),
                            ("c", "Collection"),
                        ],
                        max_length=1,
                    ),
                ),
                ("category", models.CharField(max_length=255)),
                (
                    "parent_collection",
                    models.ForeignKey(
                        on_delete=models.PROTECT,
                        related_name="child_items",
                        to="db.collection",
                    ),
                ),
                ("parent_category", models.CharField(max_length=255)),
                ("data", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("removed_at", models.DateTimeField(blank=True, null=True)),
                (
                    "artifact",
                    models.ForeignKey(
                        null=True,
                        on_delete=models.PROTECT,
                        related_name="collection_items",
                        to="db.artifact",
                    ),
                ),
                (
                    "collection",
                    models.ForeignKey(
                        null=True,
                        on_delete=models.PROTECT,
                        related_name="collection_items",
                        to="db.collection",
                    ),
                ),
                (
                    "created_by_user",
                    models.ForeignKey(
                        on_delete=models.PROTECT,
                        related_name="user_created_%(class)s",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "removed_by_user",
                    models.ForeignKey(
                        null=True,
                        on_delete=models.PROTECT,
                        related_name="user_removed_%(class)s",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "constraints": [
                    models.CheckConstraint(
                        check=models.Q(
                            ("collection", models.F("parent_collection")),
                            _negated=True,
                        ),
                        name="db_collectionitem_distinct_parent_collection",
                    ),
                    models.CheckConstraint(
                        check=models.Q(
                            models.Q(
                                ("artifact__isnull", True),
                                ("child_type", "b"),
                                ("collection__isnull", True),
                            ),
                            models.Q(
                                ("child_type", "a"),
                                ("collection__isnull", True),
                                models.Q(
                                    ("artifact__isnull", False),
                                    ("removed_at__isnull", False),
                                    _connector="OR",
                                ),
                            ),
                            models.Q(
                                ("artifact__isnull", True),
                                ("child_type", "c"),
                                models.Q(
                                    ("collection__isnull", False),
                                    ("removed_at__isnull", False),
                                    _connector="OR",
                                ),
                            ),
                            _connector="OR",
                        ),
                        name="db_collectionitem_childtype_removedat_consistent",
                    ),
                ],
                "indexes": [
                    models.Index(
                        models.F("parent_collection"),
                        KeyTextTransform("package", "data"),
                        KeyTextTransform("version", "data"),
                        condition=models.Q(
                            ("category", ArtifactCategory["SOURCE_PACKAGE"]),
                            ("child_type", "a"),
                            ("parent_category", CollectionCategory["SUITE"]),
                        ),
                        name="db_ci_suite_source_idx",
                    ),
                    models.Index(
                        models.F("parent_collection"),
                        KeyTextTransform("srcpkg_name", "data"),
                        KeyTextTransform("srcpkg_version", "data"),
                        condition=models.Q(
                            ("category", ArtifactCategory["BINARY_PACKAGE"]),
                            ("child_type", "a"),
                            ("parent_category", CollectionCategory["SUITE"]),
                        ),
                        name="db_ci_suite_binary_source_idx",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="CollectionItemMatchConstraint",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "collection",
                    models.ForeignKey(
                        on_delete=models.CASCADE,
                        related_name="item_match_constraints",
                        to="db.collection",
                    ),
                ),
                ("collection_item_id", models.BigIntegerField()),
                ("constraint_name", models.CharField(max_length=255)),
                ("key", models.TextField()),
                ("value", models.TextField()),
            ],
            options={
                "constraints": [
                    ExclusionConstraint(
                        expressions=(
                            (models.F("collection"), "="),
                            (models.F("constraint_name"), "="),
                            (models.F("key"), "="),
                            (models.F("value"), "<>"),
                        ),
                        name="db_collectionitemmatchconstraint_match_value",
                    )
                ],
                "indexes": [
                    models.Index(
                        fields=["collection_item_id"],
                        name="db_cimc_collection_item_idx",
                    )
                ],
            },
        ),
        migrations.CreateModel(
            name="FileInArtifact",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "artifact",
                    models.ForeignKey(
                        on_delete=models.PROTECT, to="db.artifact"
                    ),
                ),
                ("path", models.CharField(max_length=500)),
                (
                    "file",
                    models.ForeignKey(on_delete=models.PROTECT, to="db.file"),
                ),
                ("complete", models.BooleanField(default=False)),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        fields=("artifact", "path"),
                        name="db_fileinartifact_unique_artifact_path",
                    )
                ]
            },
        ),
        migrations.CreateModel(
            name="FileInStore",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "store",
                    models.ForeignKey(
                        on_delete=models.PROTECT, to="db.filestore"
                    ),
                ),
                (
                    "file",
                    models.ForeignKey(on_delete=models.PROTECT, to="db.file"),
                ),
                ("data", models.JSONField(blank=True, default=dict)),
            ],
            options={
                "triggers": [
                    Trigger(
                        name="db_fileinstore_total_size_insert",
                        sql=UpsertTriggerSql(
                            func="UPDATE db_filestore SET total_size = total_size + db_file.size FROM db_file WHERE db_filestore.id = NEW.store_id AND db_file.id = NEW.file_id; RETURN NULL;",
                            hash="93dd6bf857752d47e15ce24887b0151f07f5aa77",
                            operation="INSERT",
                            pgid="pgtrigger_db_fileinstore_total_size_insert_8cd7f",
                            table="db_fileinstore",
                            when="AFTER",
                        ),
                    ),
                    Trigger(
                        name="db_fileinstore_total_size_delete",
                        sql=UpsertTriggerSql(
                            func="UPDATE db_filestore SET total_size = total_size - db_file.size FROM db_file WHERE db_filestore.id = OLD.store_id AND db_file.id = OLD.file_id; RETURN NULL;",
                            hash="1d8bad11847895fe36cd12b11d4f1a10fd451da7",
                            operation="DELETE",
                            pgid="pgtrigger_db_fileinstore_total_size_delete_07c98",
                            table="db_fileinstore",
                            when="AFTER",
                        ),
                    ),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("store", "file"),
                        name="db_fileinstore_unique_store_file",
                    )
                ],
            },
        ),
        migrations.CreateModel(
            name="FileUpload",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "path",
                    models.CharField(
                        help_text="Path in the uploads directory",
                        max_length=500,
                        unique=True,
                    ),
                ),
                ("last_activity_at", models.DateTimeField(auto_now_add=True)),
                (
                    "file_in_artifact",
                    models.OneToOneField(
                        on_delete=models.PROTECT, to="db.fileinartifact"
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="Identity",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        null=True,
                        on_delete=models.SET_NULL,
                        related_name="identities",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "issuer",
                    models.CharField(
                        help_text="identifier of auhoritative system for this identity",
                        max_length=512,
                    ),
                ),
                (
                    "subject",
                    models.CharField(
                        help_text="identifier of the user in the issuer system",
                        max_length=512,
                    ),
                ),
                (
                    "last_used",
                    models.DateTimeField(
                        auto_now=True,
                        help_text="last time this identity has been used",
                    ),
                ),
                ("claims", models.JSONField(default=dict)),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        fields=("issuer", "subject"),
                        name="db_identity_unique_issuer_subject",
                    )
                ]
            },
        ),
        migrations.CreateModel(
            name="NotificationChannel",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "name",
                    models.CharField(
                        max_length=20,
                        unique=True,
                        validators=[validate_notification_channel_name],
                    ),
                ),
                (
                    "method",
                    models.CharField(
                        choices=[("email", "Email")], max_length=10
                    ),
                ),
                ("data", models.JSONField(blank=True, default=dict)),
            ],
        ),
        migrations.CreateModel(
            name="Token",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "hash",
                    models.CharField(
                        max_length=64,
                        unique=True,
                        validators=[
                            MaxLengthValidator(64),
                            MinLengthValidator(64),
                        ],
                        verbose_name="Hexadecimal hash, length is 64 chars",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=models.PROTECT,
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "comment",
                    models.CharField(
                        blank=True,
                        default="",
                        max_length=100,
                        verbose_name="Reason that this token was created",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("expire_at", models.DateTimeField(blank=True, null=True)),
                ("enabled", models.BooleanField(default=False)),
                (
                    "last_seen_at",
                    models.DateTimeField(
                        blank=True,
                        help_text="Last time that the token was used",
                        null=True,
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="WorkerPool",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "name",
                    models.SlugField(
                        help_text="Human readable name of the worker pool",
                        unique=True,
                    ),
                ),
                ("enabled", models.BooleanField(default=True)),
                ("architectures", models.JSONField(default=list)),
                ("tags", models.JSONField(blank=True, default=list)),
                ("specifications", models.JSONField(default=dict)),
                ("instance_wide", models.BooleanField(default=True)),
                ("ephemeral", models.BooleanField(default=False)),
                ("limits", models.JSONField(blank=True, default=dict)),
                ("registered_at", models.DateTimeField()),
                (
                    "provider_account",
                    models.ForeignKey(on_delete=models.PROTECT, to="db.asset"),
                ),
            ],
        ),
        migrations.CreateModel(
            name="ScopeWorkerPool",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("priority", models.IntegerField(default=0)),
                ("limits", models.JSONField(blank=True, default=dict)),
                (
                    "scope",
                    models.ForeignKey(on_delete=models.CASCADE, to="db.scope"),
                ),
                (
                    "worker_pool",
                    models.ForeignKey(
                        on_delete=models.CASCADE, to="db.workerpool"
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="Worker",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "name",
                    models.SlugField(
                        help_text="Human readable name of the worker based on the FQDN",
                        unique=True,
                    ),
                ),
                ("registered_at", models.DateTimeField()),
                ("connected_at", models.DateTimeField(blank=True, null=True)),
                (
                    "instance_created_at",
                    models.DateTimeField(blank=True, null=True),
                ),
                (
                    "token",
                    models.OneToOneField(
                        null=True,
                        on_delete=models.PROTECT,
                        related_name="worker",
                        to="db.token",
                    ),
                ),
                (
                    "activation_token",
                    models.OneToOneField(
                        null=True,
                        on_delete=models.SET_NULL,
                        related_name="activating_worker",
                        to="db.token",
                    ),
                ),
                ("static_metadata", models.JSONField(blank=True, default=dict)),
                (
                    "dynamic_metadata",
                    models.JSONField(blank=True, default=dict),
                ),
                (
                    "dynamic_metadata_updated_at",
                    models.DateTimeField(blank=True, null=True),
                ),
                (
                    "worker_type",
                    models.CharField(
                        choices=[
                            (WorkerType["EXTERNAL"], WorkerType["EXTERNAL"]),
                            (WorkerType["CELERY"], WorkerType["CELERY"]),
                            (WorkerType["SIGNING"], WorkerType["SIGNING"]),
                        ],
                        default=WorkerType["EXTERNAL"],
                        editable=False,
                        max_length=8,
                    ),
                ),
                (
                    "concurrency",
                    models.PositiveIntegerField(
                        default=1,
                        help_text="Number of tasks this worker can run simultaneously",
                    ),
                ),
                (
                    "worker_pool",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=models.PROTECT,
                        to="db.workerpool",
                    ),
                ),
                ("worker_pool_data", models.JSONField(blank=True, null=True)),
            ],
            options={
                "constraints": [
                    models.CheckConstraint(
                        check=models.Q(
                            ("worker_type", WorkerType["CELERY"]),
                            ("activation_token__isnull", False),
                            ("token__isnull", False),
                            _connector="OR",
                        ),
                        name="db_worker_celery_or_token",
                    )
                ]
            },
        ),
        migrations.CreateModel(
            name="WorkerPoolStatistics",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("timestamp", models.DateTimeField(auto_now_add=True)),
                ("runtime", models.IntegerField()),
                (
                    "worker",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=models.SET_NULL,
                        to="db.worker",
                    ),
                ),
                (
                    "worker_pool",
                    models.ForeignKey(
                        on_delete=models.CASCADE, to="db.workerpool"
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(
                        models.F("timestamp"), name="db_worker_pool_stat_ts_idx"
                    )
                ]
            },
        ),
        migrations.CreateModel(
            name="WorkerPoolTaskExecutionStatistics",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("timestamp", models.DateTimeField(auto_now_add=True)),
                ("runtime", models.IntegerField()),
                (
                    "scope",
                    models.ForeignKey(on_delete=models.CASCADE, to="db.scope"),
                ),
                (
                    "worker",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=models.SET_NULL,
                        to="db.worker",
                    ),
                ),
                (
                    "worker_pool",
                    models.ForeignKey(
                        on_delete=models.CASCADE, to="db.workerpool"
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(
                        models.F("timestamp"), name="db_worker_pool_exec_ts_idx"
                    )
                ]
            },
        ),
        migrations.CreateModel(
            name="WorkflowTemplate",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("name", models.CharField(max_length=255)),
                (
                    "workspace",
                    models.ForeignKey(
                        on_delete=models.PROTECT, to="db.workspace"
                    ),
                ),
                (
                    "task_name",
                    models.CharField(
                        max_length=100,
                        verbose_name="Name of the Workflow orchestrator class",
                    ),
                ),
                (
                    "task_data",
                    models.JSONField(
                        blank=True, default=dict, encoder=DjangoJSONEncoder
                    ),
                ),
                (
                    "priority",
                    models.IntegerField(
                        default=0,
                        help_text="Base priority for work requests created from this template",
                    ),
                ),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        fields=("name", "workspace"),
                        name="db_workflowtemplate_unique_name_workspace",
                    )
                ]
            },
        ),
        migrations.CreateModel(
            name="WorkRequest",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "workspace",
                    models.ForeignKey(
                        on_delete=models.PROTECT, to="db.workspace"
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                (
                    "workflow_last_activity_at",
                    models.DateTimeField(blank=True, null=True),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        on_delete=models.PROTECT, to=settings.AUTH_USER_MODEL
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("running", "Running"),
                            ("completed", "Completed"),
                            ("aborted", "Aborted"),
                            ("blocked", "Blocked"),
                        ],
                        default="pending",
                        editable=False,
                        max_length=9,
                    ),
                ),
                (
                    "workflow_runtime_status",
                    models.CharField(
                        blank=True,
                        choices=[
                            ("needs_input", "Needs Input"),
                            ("running", "Running"),
                            ("waiting", "Waiting"),
                            ("pending", "Pending"),
                            ("aborted", "Aborted"),
                            ("completed", "Completed"),
                            ("blocked", "Blocked"),
                        ],
                        editable=False,
                        max_length=11,
                        null=True,
                    ),
                ),
                ("workflows_need_update", models.BooleanField(default=False)),
                (
                    "result",
                    models.CharField(
                        choices=[
                            ("", ""),
                            ("success", "Success"),
                            ("failure", "Failure"),
                            ("error", "Error"),
                        ],
                        default="",
                        editable=False,
                        max_length=7,
                    ),
                ),
                (
                    "worker",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=models.CASCADE,
                        related_name="assigned_work_requests",
                        to="db.worker",
                    ),
                ),
                (
                    "task_type",
                    models.CharField(
                        choices=[
                            (TaskTypes["WORKER"], TaskTypes["WORKER"]),
                            (TaskTypes["SERVER"], TaskTypes["SERVER"]),
                            (TaskTypes["INTERNAL"], TaskTypes["INTERNAL"]),
                            (TaskTypes["WORKFLOW"], TaskTypes["WORKFLOW"]),
                            (TaskTypes["SIGNING"], TaskTypes["SIGNING"]),
                            (TaskTypes["WAIT"], TaskTypes["WAIT"]),
                        ],
                        default=TaskTypes["WORKER"],
                        editable=False,
                        max_length=8,
                        verbose_name="Type of task to execute",
                    ),
                ),
                (
                    "task_name",
                    models.CharField(
                        max_length=100,
                        verbose_name="Name of the task to execute",
                    ),
                ),
                (
                    "task_data",
                    models.JSONField(
                        blank=True, default=dict, encoder=DjangoJSONEncoder
                    ),
                ),
                (
                    "dynamic_task_data",
                    models.JSONField(
                        blank=True, encoder=DjangoJSONEncoder, null=True
                    ),
                ),
                (
                    "priority_base",
                    models.IntegerField(
                        default=0,
                        help_text="Base priority of this work request",
                    ),
                ),
                (
                    "priority_adjustment",
                    models.IntegerField(
                        default=0,
                        help_text="Administrator adjustment to the priority of this work request",
                    ),
                ),
                (
                    "output_data_json",
                    models.JSONField(
                        blank=True,
                        db_column="output_data",
                        encoder=DjangoJSONEncoder,
                        null=True,
                    ),
                ),
                (
                    "unblock_strategy",
                    models.CharField(
                        choices=[
                            ("deps", "Dependencies have completed"),
                            ("manual", "Manually unblocked"),
                        ],
                        default="deps",
                        editable=False,
                        max_length=6,
                    ),
                ),
                (
                    "workflow_template",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=models.SET_NULL,
                        to="db.workflowtemplate",
                    ),
                ),
                (
                    "workflow_data_json",
                    models.JSONField(
                        blank=True,
                        db_column="workflow_data",
                        default=dict,
                        encoder=DjangoJSONEncoder,
                    ),
                ),
                (
                    "event_reactions_json",
                    models.JSONField(
                        blank=True,
                        db_column="event_reactions",
                        default=dict,
                        encoder=DjangoJSONEncoder,
                    ),
                ),
                (
                    "internal_collection",
                    models.OneToOneField(
                        blank=True,
                        null=True,
                        on_delete=models.PROTECT,
                        related_name="workflow",
                        to="db.collection",
                    ),
                ),
                (
                    "expiration_delay",
                    models.DurationField(blank=True, null=True),
                ),
                (
                    "supersedes",
                    models.OneToOneField(
                        blank=True,
                        null=True,
                        on_delete=models.SET_NULL,
                        related_name="superseded",
                        to="db.workrequest",
                    ),
                ),
                (
                    "configured_task_data",
                    models.JSONField(
                        blank=True,
                        encoder=DjangoJSONEncoder,
                        help_text="task_data with task configuration applied",
                        null=True,
                    ),
                ),
                (
                    "version",
                    models.IntegerField(
                        default=0,
                        help_text="version of the code that computed the dynamic task data",
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(
                        models.OrderBy(
                            CombinedExpression(
                                models.F("priority_base"),
                                "+",
                                models.F("priority_adjustment"),
                            ),
                            descending=True,
                        ),
                        models.F("created_at"),
                        condition=models.Q(("status", "pending")),
                        name="db_workrequest_pending_idx",
                    ),
                    models.Index(
                        models.F("worker"),
                        condition=models.Q(
                            ("status__in", ("pending", "running"))
                        ),
                        name="db_workrequest_worker_idx",
                    ),
                    models.Index(
                        models.F("workflows_need_update"),
                        name="db_workrequest_wf_update_idx",
                    ),
                ],
                "permissions": [
                    (
                        "manage_workrequest_priorities",
                        "Can set positive priority adjustments on work requests",
                    )
                ],
                "ordering": ["id"],
            },
        ),
        migrations.AddField(
            model_name="asset",
            name="created_by_work_request",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.SET_NULL,
                to="db.workrequest",
            ),
        ),
        migrations.AddConstraint(
            model_name="asset",
            constraint=JsonDataUniqueConstraint(
                condition=models.Q(
                    ("category", AssetCategory["CLOUD_PROVIDER_ACCOUNT"])
                ),
                fields=("data->>'name'",),
                name="db_asset_unique_cloud_provider_acct_name",
                nulls_distinct=False,
            ),
        ),
        migrations.AddConstraint(
            model_name="asset",
            constraint=JsonDataUniqueConstraint(
                condition=models.Q(("category", AssetCategory["SIGNING_KEY"])),
                fields=("data->>'fingerprint'",),
                name="db_asset_unique_signing_key_fingerprints",
                nulls_distinct=False,
            ),
        ),
        migrations.AddField(
            model_name="artifact",
            name="created_by_work_request",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.SET_NULL,
                to="db.workrequest",
            ),
        ),
        migrations.AddField(
            model_name="artifact",
            name="files",
            field=models.ManyToManyField(
                through="db.FileInArtifact", to="db.file"
            ),
        ),
        migrations.AddField(
            model_name="collection",
            name="child_artifacts",
            field=models.ManyToManyField(
                related_name="parent_collections",
                through="db.CollectionItem",
                to="db.artifact",
            ),
        ),
        migrations.AddField(
            model_name="collection",
            name="child_collections",
            field=models.ManyToManyField(
                related_name="parent_collections",
                through="db.CollectionItem",
                to="db.collection",
            ),
        ),
        migrations.AddField(
            model_name="collectionitem",
            name="created_by_workflow",
            field=models.ForeignKey(
                null=True,
                on_delete=models.PROTECT,
                related_name="workflow_created_%(class)s",
                to="db.workrequest",
            ),
        ),
        migrations.AddField(
            model_name="collectionitem",
            name="removed_by_workflow",
            field=models.ForeignKey(
                null=True,
                on_delete=models.PROTECT,
                related_name="workflow_removed_%(class)s",
                to="db.workrequest",
            ),
        ),
        migrations.AddConstraint(
            model_name="collectionitem",
            constraint=models.UniqueConstraint(
                condition=models.Q(("removed_at__isnull", True)),
                fields=("name", "parent_collection"),
                name="db_collectionitem_unique_active_name",
            ),
        ),
        migrations.AddConstraint(
            model_name="collectionitem",
            constraint=JsonDataUniqueConstraint(
                condition=models.Q(
                    ("parent_category", CollectionCategory["ENVIRONMENTS"]),
                    ("removed_at__isnull", True),
                ),
                fields=(
                    "category",
                    "data->>'codename'",
                    "data->>'architecture'",
                    "data->>'variant'",
                    "data->>'backend'",
                    "parent_collection",
                ),
                name="db_collectionitem_unique_debian_environments",
                nulls_distinct=False,
            ),
        ),
        migrations.AddField(
            model_name="filestore",
            name="files",
            field=models.ManyToManyField(
                through="db.FileInStore", to="db.file"
            ),
        ),
        migrations.AddField(
            model_name="filestore",
            name="provider_account",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=models.PROTECT, to="db.asset"
            ),
        ),
        migrations.AddField(
            model_name="group",
            name="users",
            field=models.ManyToManyField(
                blank=True,
                related_name="debusine_groups",
                through="db.GroupMembership",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="scope",
            name="file_stores",
            field=models.ManyToManyField(
                related_name="scopes",
                through="db.FileStoreInScope",
                to="db.filestore",
            ),
        ),
        migrations.AddField(
            model_name="workrequest",
            name="dependencies",
            field=models.ManyToManyField(
                related_name="reverse_dependencies", to="db.workrequest"
            ),
        ),
        migrations.AddField(
            model_name="workrequest",
            name="parent",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.CASCADE,
                related_name="children",
                to="db.workrequest",
            ),
        ),
        migrations.AddField(
            model_name="workspace",
            name="inherits",
            field=models.ManyToManyField(
                related_name="inherited_by",
                through="db.WorkspaceChain",
                to="db.workspace",
            ),
        ),
        migrations.RunPython(create_default_objects),
    ]
