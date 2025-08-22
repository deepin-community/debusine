# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Database models for the Debusine signing service."""

from collections.abc import Sequence
from pathlib import Path
from typing import Any, BinaryIO, ClassVar, TYPE_CHECKING, TypeAlias

from django.conf import settings
from django.db import models

from debusine.assets import KeyPurpose
from debusine.signing.models import ProtectedKey, ProtectedKeyNaCl, SigningMode
from debusine.signing.openssl import openssl_generate, x509_fingerprint
from debusine.signing.sbsign import sbsign
from debusine.utils import calculate_hash

if TYPE_CHECKING:
    from django_stubs_ext.db.models import TypedModelMeta
else:
    TypedModelMeta = object


class AuditLog(models.Model):
    """An audit log entry."""

    objects: ClassVar[models.Manager["AuditLog"]] = models.Manager["AuditLog"]()

    class Event(models.TextChoices):
        GENERATE = "generate", "Generate"
        SIGN = "sign", "Sign"
        REGISTER = "register", "Register an externally-generated key"

    purpose = models.CharField(max_length=7, choices=KeyPurpose.choices)
    fingerprint = models.CharField(max_length=64)
    event = models.CharField(max_length=8, choices=Event.choices)
    data = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)
    created_by_work_request_id = models.IntegerField(blank=True, null=True)

    class Meta(TypedModelMeta):
        app_label = "signing"
        indexes = [
            models.Index(
                "purpose",
                "fingerprint",
                name="%(app_label)s_%(class)s_key_idx",
            ),
            models.Index(
                "created_by_work_request_id",
                name="%(app_label)s_%(class)s_wr_idx",
            ),
        ]


class KeyManager(models.Manager["Key"]):
    """Manager for the Key model."""

    def get_fingerprint(
        self,
        *,
        purpose: KeyPurpose,
        public_key: bytes,
        log_file: BinaryIO | None = None,
    ) -> str:
        """Get the fingerprint for a public key."""
        match purpose:
            case KeyPurpose.UEFI:
                return x509_fingerprint(public_key, log_file=log_file)
            case KeyPurpose.OPENPGP:
                # Imported late because the gpg module has to be installed as a
                # .deb.
                from debusine.signing.gnupg import gpg_fingerprint

                return gpg_fingerprint(public_key)
            case _ as unreachable:
                raise AssertionError(f"Unexpected purpose: {unreachable}")

    def generate(
        self,
        purpose: KeyPurpose,
        description: str,
        created_by_work_request_id: int,
        log_file: BinaryIO | None = None,
    ) -> "Key":
        """Generate a new key."""
        match purpose:
            case KeyPurpose.UEFI:
                # For now we just hardcode the certificate lifetime: 15
                # years is enough to extend from the start of the
                # development period of a Debian release to the end of
                # Freexian's extended LTS period.
                days = 15 * 365
                private_key, public_key = openssl_generate(
                    description, days, log_file=log_file
                )
            case KeyPurpose.OPENPGP:
                # Imported late because the gpg module has to be installed as a
                # .deb.
                from debusine.signing.gnupg import gpg_generate

                private_key, public_key = gpg_generate(description)
            case _ as unreachable:
                raise AssertionError(f"Unexpected purpose: {unreachable}")

        fingerprint = self.get_fingerprint(
            purpose=purpose, public_key=public_key, log_file=log_file
        )
        key = self.create(
            purpose=purpose,
            fingerprint=fingerprint,
            private_key=ProtectedKeyNaCl.encrypt(
                settings.DEBUSINE_SIGNING_PRIVATE_KEYS[0].public_key,
                private_key,
            ).dict(),
            public_key=public_key,
        )
        AuditLog.objects.create(
            event=AuditLog.Event.GENERATE,
            purpose=purpose,
            fingerprint=fingerprint,
            data={"description": description},
            created_by_work_request_id=created_by_work_request_id,
        )
        return key


class Key(models.Model):
    """A key managed by the debusine signing service."""

    objects = KeyManager()

    Purpose: TypeAlias = KeyPurpose

    purpose = models.CharField(max_length=7, choices=Purpose.choices)
    fingerprint = models.CharField(max_length=64)
    private_key = models.JSONField()
    public_key = models.BinaryField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(blank=True, null=True)

    class Meta(TypedModelMeta):
        app_label = "signing"
        constraints = [
            models.UniqueConstraint(
                fields=("purpose", "fingerprint"),
                name="%(app_label)s_%(class)s_unique_purpose_fingerprint",
            )
        ]

    @property
    def stored_private_key(self) -> ProtectedKey:
        """Access private_key as a pydantic model."""
        return ProtectedKey.parse_obj(self.private_key)


def sign(
    keys: Sequence["Key"],
    data_path: Path,
    signature_path: Path,
    mode: SigningMode,
    created_by_work_request_id: int,
    username: str | None = None,
    user_id: int | None = None,
    resource: dict[str, Any] | None = None,
    log_file: BinaryIO | None = None,
) -> None:
    """
    Sign data in `data_path` using `keys`.

    `signature_path` will be overwritten with the resulting signature.
    """
    purposes = list({key.purpose for key in keys})
    assert len(purposes) == 1
    [purpose] = purposes

    match (purpose, mode):
        case KeyPurpose.UEFI, SigningMode.ATTACHED | SigningMode.DETACHED:
            assert len(keys) == 1
            sbsign(
                keys[0].stored_private_key,
                keys[0].public_key,
                data_path=data_path,
                signature_path=signature_path,
                detached=(mode == SigningMode.DETACHED),
                log_file=log_file,
            )
        case KeyPurpose.OPENPGP, SigningMode.DETACHED | SigningMode.CLEAR:
            # Imported late because the gpg module has to be installed as a
            # .deb.
            from debusine.signing.gnupg import gpg_sign

            gpg_sign(
                [(key.stored_private_key, key.public_key) for key in keys],
                data_path=data_path,
                signature_path=signature_path,
                mode=mode,
            )
        case KeyPurpose.OPENPGP, SigningMode.DEBSIGN:
            # Imported late because the gpg module has to be installed as a
            # .deb.
            from debusine.signing.gnupg import gpg_debsign

            assert len(keys) == 1
            gpg_debsign(
                keys[0].stored_private_key,
                keys[0].public_key,
                keys[0].fingerprint,
                data_path=data_path,
                signature_path=signature_path,
                log_file=log_file,
            )
        case _ as unreachable:
            raise AssertionError(f"Unexpected purpose and mode: {unreachable}")

    audit_log_data: dict[str, Any] = {
        "data_sha256": calculate_hash(data_path, "sha256").hex(),
    }
    if resource:
        audit_log_data["resource"] = resource
    if username:
        audit_log_data["username"] = username
    if user_id:
        audit_log_data["user_id"] = user_id

    AuditLog.objects.bulk_create(
        [
            AuditLog(
                event=AuditLog.Event.SIGN,
                purpose=purpose,
                fingerprint=key.fingerprint,
                data=audit_log_data,
                created_by_work_request_id=created_by_work_request_id,
            )
            for key in keys
        ]
    )
