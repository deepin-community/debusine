# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Signing service data models."""

import base64
from abc import ABC, abstractmethod
from collections.abc import Generator, Iterable
from contextlib import contextmanager
from enum import StrEnum
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Annotated, Literal, Self, TYPE_CHECKING

from django.conf import settings

try:
    import pydantic.v1 as pydantic
except ImportError:
    import pydantic as pydantic  # type: ignore

from debusine.signing.signing_utils import SensitiveTemporaryDirectory

if TYPE_CHECKING:
    from nacl.public import PrivateKey, PublicKey


class ProtectedKeyError(Exception):
    """An error handling a protected key."""


class ProtectedKeyStorage(StrEnum):
    """Possible values for ProtectedKey.storage."""

    NACL = "nacl"
    PKCS11_STATIC = "pkcs11-static"


class BaseAvailableKey(ABC):
    """A protected key that has temporarily been made available."""

    def __init__(self, *, temp_dir: Path, certificate: bytes) -> None:
        """Construct a :py:class:`BaseAvailableKey`."""
        self.temp_dir = temp_dir
        self._certificate_path.write_bytes(certificate)

    @property
    def _certificate_path(self) -> Path:
        return self.temp_dir / "tmp.crt"

    @property
    @abstractmethod
    def sbsign_args(self) -> list[str]:
        """Arguments for `sbsign`."""


class BaseProtectedKey(pydantic.BaseModel, ABC):
    """Base class for protected private keys."""

    storage: ProtectedKeyStorage

    @contextmanager
    @abstractmethod
    def available(
        self, public_key: bytes
    ) -> Generator[BaseAvailableKey, None, None]:
        """
        Make a protected key temporarily available.

        Controls on the available key may vary depending on the type of
        protection used, but they should at least ensure that private keys
        do not end up on a persistent file system.
        """


class AvailableKeyFileSystem(BaseAvailableKey):
    """An available private key on the file system."""

    def __init__(
        self, *, temp_dir: Path, certificate: bytes, key: bytes
    ) -> None:
        """Construct an :py:class:`AvailableKeyFileSystem`."""
        super().__init__(temp_dir=temp_dir, certificate=certificate)
        self._key_path.write_bytes(key)

    @property
    def _key_path(self) -> Path:
        return self.temp_dir / "tmp.key"

    @property
    def sbsign_args(self) -> list[str]:
        """Arguments for `sbsign`."""
        return [
            "--key",
            str(self._key_path),
            "--cert",
            str(self._certificate_path),
        ]


class AvailableKeyPKCS11(BaseAvailableKey):
    """An available private key on a PKCS#11 token."""

    def __init__(
        self, *, temp_dir: Path, certificate: bytes, pkcs11_uri: str
    ) -> None:
        """Construct an :py:class:`AvailableKeyPKCS11`."""
        super().__init__(temp_dir=temp_dir, certificate=certificate)
        self.pkcs11_uri = pkcs11_uri

    @property
    def sbsign_args(self) -> list[str]:
        """Arguments for `sbsign`."""
        return [
            "--engine",
            "pkcs11",
            "--key",
            self.pkcs11_uri,
            "--cert",
            str(self._certificate_path),
        ]


class ProtectedKeyNaCl(BaseProtectedKey):
    """Data for a private key encrypted in software using NaCl."""

    storage: Literal[ProtectedKeyStorage.NACL]
    public_key: str
    encrypted: str

    @classmethod
    def create(cls, *, public_key: str | bytes, encrypted: str | bytes) -> Self:
        """Create a new :py:class:`ProtectedKeyNaCl`."""
        return cls(
            storage=ProtectedKeyStorage.NACL,
            public_key=(
                public_key
                if isinstance(public_key, str)
                else base64.b64encode(public_key).decode()
            ),
            encrypted=(
                encrypted
                if isinstance(encrypted, str)
                else base64.b64encode(encrypted).decode()
            ),
        )

    @property
    def public_key_bytes(self) -> bytes:
        """Return the base64-decoded public key."""
        return base64.b64decode(self.public_key.encode())

    @property
    def encrypted_bytes(self) -> bytes:
        """Return the base64-decoded encrypted data."""
        return base64.b64decode(self.encrypted.encode())

    @classmethod
    def encrypt(cls, public_key: "PublicKey", data: bytes) -> Self:
        """Encrypt data using a NaCl public key."""
        from nacl.public import SealedBox

        try:
            encrypted = SealedBox(public_key).encrypt(data)
        except Exception as e:
            raise ProtectedKeyError(str(e)) from e
        return cls.create(public_key=bytes(public_key), encrypted=encrypted)

    def decrypt(self, private_keys: Iterable["PrivateKey"]) -> bytes:
        """
        Decrypt data using any of an iterable of NaCl private keys.

        This uses the private key that matches the stored public key, if one
        exists.  This allows for key rotation.

        :raises ProtectedKeyError: if none of the given private keys match
          the stored public key.
        """
        from nacl.exceptions import CryptoError
        from nacl.public import SealedBox

        public_key_bytes = self.public_key_bytes
        for private_key in private_keys:
            if bytes(private_key.public_key) == public_key_bytes:
                try:
                    return SealedBox(private_key).decrypt(self.encrypted_bytes)
                except CryptoError as e:
                    raise ProtectedKeyError(str(e)) from e
        raise ProtectedKeyError(
            "Key not encrypted using any of the given private keys"
        )

    @contextmanager
    def available(
        self, public_key: bytes
    ) -> Generator[BaseAvailableKey, None, None]:
        """Make this protected key temporarily available."""
        with SensitiveTemporaryDirectory(
            "debusine-available-nacl-"
        ) as temp_dir:
            yield AvailableKeyFileSystem(
                temp_dir=Path(temp_dir),
                certificate=public_key,
                key=self.decrypt(settings.DEBUSINE_SIGNING_PRIVATE_KEYS),
            )


class ProtectedKeyPKCS11Static(BaseProtectedKey):
    """
    Data for a private key held on a PKCS#11 token.

    This key is not extracted under wrap; it only ever lives on the token.
    """

    storage: Literal[ProtectedKeyStorage.PKCS11_STATIC]
    pkcs11_uri: str

    @classmethod
    def create(cls, *, pkcs11_uri: str) -> Self:
        """Create a new :py:class:`ProtectedKeyPKCS11Static`."""
        return cls(
            storage=ProtectedKeyStorage.PKCS11_STATIC, pkcs11_uri=pkcs11_uri
        )

    @pydantic.validator("pkcs11_uri")
    @classmethod
    def _pkcs11_uri_is_pkcs11(cls, value: str) -> str:
        """Ensure that the PKCS#11 URI uses the "pkcs11" scheme."""
        if not value.startswith("pkcs11:"):
            raise ValueError("pkcs11_uri must start with 'pkcs11:'")
        return value

    @contextmanager
    def available(
        self, public_key: bytes
    ) -> Generator[BaseAvailableKey, None, None]:
        """Make this protected key temporarily available."""
        with TemporaryDirectory(
            "debusine-available-pkcs11-static-"
        ) as temp_dir:
            yield AvailableKeyPKCS11(
                temp_dir=Path(temp_dir),
                certificate=public_key,
                pkcs11_uri=self.pkcs11_uri,
            )


class ProtectedKey(pydantic.BaseModel):
    """A protected private key."""

    __root__: Annotated[
        ProtectedKeyNaCl | ProtectedKeyPKCS11Static,
        pydantic.Field(discriminator="storage"),
    ]

    @contextmanager
    def available(
        self, public_key: bytes
    ) -> Generator[BaseAvailableKey, None, None]:
        """
        Make a protected key temporarily available.

        Controls on the available key may vary depending on the type of
        protection used, but they should at least ensure that private keys
        do not end up on a persistent file system.
        """
        with self.__root__.available(public_key) as available:
            yield available


class SigningMode(StrEnum):
    """
    The kind of signing to perform.

    Only some combinations of key purpose and signing mode are valid.
    """

    # Return the original data with an attached signature.
    ATTACHED = "attached"

    # Return a detached signature.
    DETACHED = "detached"

    # Return the original data with an attached signature, encapsulated as
    # plain text.  This is only valid for OpenPGP.
    CLEAR = "clear"

    # Sign a complete Debian upload using `debsign` or equivalent.
    DEBSIGN = "debsign"
