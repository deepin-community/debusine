# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Test for the main entry point of debusine."""

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, NotRequired, TypedDict

try:
    import pydantic.v1 as pydantic
except ImportError:
    import pydantic  # type: ignore

from debusine.assets import AssetCategory, KeyPurpose, SigningKeyData
from debusine.client.models import (
    ArtifactCreateRequest,
    AssetResponse,
    AssetsResponse,
    EnrollConfirmPayload,
    EnrollOutcome,
    EnrollPayload,
    FileRequest,
    FilesRequestType,
    LookupMultipleResponse,
    LookupResultType,
    LookupSingleResponse,
    LookupSingleResponseArtifact,
    RelationCreateRequest,
    RelationResponse,
    RelationType,
    RelationsResponse,
    StrMaxLength255,
    StrictBaseModel,
    WorkspaceInheritanceChain,
    WorkspaceInheritanceChainElement,
    model_to_json_serializable_dict,
)
from debusine.test import TestCase
from debusine.test.test_utils import (
    create_artifact_response,
    create_work_request_response,
)


class WorkRequestTests(TestCase):
    """Tests for WorkRequest."""

    def test_work_request__str__(self) -> None:
        """__str__() return WorkRequest: {_id}."""
        work_request_id = 5
        work_request = create_work_request_response(id=work_request_id)

        self.assertEqual(str(work_request), f'WorkRequest: {work_request_id}')


class FileTests(TestCase):
    """Tests for the FileRequest."""

    def setUp(self) -> None:
        """Set up testing objects."""
        super().setUp()
        self.size = 10
        self.checksums = {
            "sha256": pydantic.parse_obj_as(
                StrMaxLength255,
                "bf2cb58a68f684d95a3b78ef8f661c"
                "9a4e5b09e82cc8f9cc88cce90528caeb27",
            )
        }

        self.file = FileRequest(
            size=self.size, checksums=self.checksums, type="file"
        )

    def test_fields(self) -> None:
        """Assert expected fields."""
        self.assertEqual(self.file.size, 10)
        self.assertEqual(self.file.checksums, self.checksums)
        self.assertEqual(self.file.type, "file")

    def test_size_positive_integer(self) -> None:
        """Assert size cannot be negative."""
        with self.assertRaisesRegex(
            pydantic.ValidationError,
            r"ensure this value is greater than or equal to 0",
        ):
            FileRequest(size=-10, checksums=self.checksums, type="file")

    def test_size_zero_is_valid(self) -> None:
        """Assert size can be zero."""
        FileRequest(size=0, checksums=self.checksums, type="file")

    def test_file_type_only_file(self) -> None:
        """Assert file_type cannot be "link" (only "file")."""
        with self.assertRaisesRegex(
            pydantic.ValidationError, r"permitted: 'file'.*given=link"
        ):
            FileRequest(
                size=self.size,
                checksums=self.checksums,
                type="link",  # type: ignore[arg-type]
            )

    def test_checksum_not_bigger_than_255(self) -> None:
        """Assert checksums cannot be longer than 255."""
        checksums = {"sha256": StrMaxLength255("a" * 256)}
        with self.assertRaisesRegex(
            pydantic.ValidationError, r"max_length.*255"
        ):
            FileRequest(size=self.size, checksums=checksums, type="file")


class ArtifactCreateRequestTests(TestCase):
    """Tests for the ArtifactCreateRequest."""

    def setUp(self) -> None:
        """Set up testing objects."""
        super().setUp()
        self.category = "debian"
        self.workspace = "debian-lts"
        self.files = FilesRequestType(
            {
                "README": FileRequest(
                    size=10,
                    checksums={
                        "sha256": pydantic.parse_obj_as(StrMaxLength255, "aaaa")
                    },
                    type="file",
                )
            }
        )
        self.data: dict[str, Any] = {}
        self.expire_at = datetime.now() + timedelta(days=1)

        self.artifact = ArtifactCreateRequest(
            category=self.category,
            workspace=self.workspace,
            files=self.files,
            data=self.data,
            expire_at=self.expire_at,
        )

    def test_fields(self) -> None:
        """Assert expected fields are accessible."""
        self.assertEqual(self.artifact.category, self.category)
        self.assertEqual(self.artifact.workspace, self.workspace)
        self.assertEqual(self.artifact.files, self.files)
        self.assertEqual(self.artifact.data, self.data)
        self.assertEqual(self.artifact.expire_at, self.expire_at)

    def test_valid_file(self) -> None:
        """Assert files must contain a file type."""
        files = FilesRequestType({"README": "test"})  # type: ignore[dict-item]
        with self.assertRaises(pydantic.ValidationError):
            self.artifact = ArtifactCreateRequest(
                category=self.category,
                workspace=self.workspace,
                files=files,
                data=self.data,
            )

    def test_create(self) -> None:
        """Assert create() returns the correct model."""
        contents = b"This is a test"

        file = self.create_temporary_file(contents=contents)

        file_request = FileRequest.create_from(file)
        self.assertEqual(file_request.size, len(contents))


class ArtifactResponseTests(TestCase):
    """Tests for ArtifactResponse."""

    def setUp(self) -> None:
        """Set up objects."""
        super().setUp()
        self.artifact_id = 5
        self.files_to_upload = ["README", "src/.dirstamp"]

        self.artifact = create_artifact_response(
            id=self.artifact_id,
            category="Testing",
            workspace="some workspace",
            created_at=datetime.now(timezone.utc),
            download_tar_gz_url="https://example.com/some/path/",
            files_to_upload=self.files_to_upload,
        )

    def test_fields(self) -> None:
        """Test fields of ArtifactResponse."""
        self.assertEqual(self.artifact.id, self.artifact_id)
        self.assertEqual(self.artifact.files_to_upload, self.files_to_upload)


class RelationCreateRequestTests(TestCase):
    """Tests for RelationCreateRequest."""

    def setUp(self) -> None:
        """Set up test."""
        super().setUp()
        self.artifact_id = 1
        self.target_id = 2

        self.relation = RelationCreateRequest(
            artifact=self.artifact_id,
            target=self.target_id,
            type=RelationType.EXTENDS,
        )

    def test_create(self) -> None:
        """Test object has the expected fields."""
        self.assertEqual(self.relation.artifact, self.artifact_id)
        self.assertEqual(self.relation.target, self.target_id)
        self.assertEqual(self.relation.type, "extends")

    def test_relation_types(self) -> None:
        """No exception is raised when setting valid relations."""
        for relation_type in RelationType:
            RelationCreateRequest(
                artifact=self.artifact_id,
                target=self.target_id,
                type=relation_type,
            )

    def test_relation_type_raise_error(self) -> None:
        """Exception is raised if a non-valid relation type is set."""
        with self.assertRaises(pydantic.ValidationError):
            RelationCreateRequest(
                artifact=1,
                target=2,
                type="does-not-exist",  # type: ignore[arg-type]
            )


class RelationsResponseTests(TestCase):
    """Tests for RelationsResponse."""

    def test_iter(self) -> None:
        """`__iter__` iterates over individual relation responses."""
        response = RelationsResponse.parse_obj(
            [
                {"id": i, "artifact": i + 1, "target": i, "type": "extends"}
                for i in (1, 2)
            ]
        )

        self.assertEqual(
            list(response),
            [
                RelationResponse(
                    id=i, artifact=i + 1, target=i, type=RelationType.EXTENDS
                )
                for i in (1, 2)
            ],
        )


class LookupMultipleResponseTests(TestCase):
    """Tests for LookupMultipleResponse."""

    def test_iter(self) -> None:
        """`__iter__` iterates over individual lookup results."""
        response: LookupMultipleResponse[LookupSingleResponseArtifact] = (
            LookupMultipleResponse.parse_obj(
                [
                    {"result_type": "a", "artifact": artifact}
                    for artifact in (1, 2)
                ]
            )
        )

        self.assertEqual(
            list(response),
            [
                LookupSingleResponse(
                    result_type=LookupResultType.ARTIFACT, artifact=artifact
                )
                for artifact in (1, 2)
            ],
        )


class StrictBaseClass(StrictBaseModel):
    """Class to instantiate in StrictBaseModelTests."""

    id: int


class StrictBaseModelTests(TestCase):
    """Tests for StrictBaseModel."""

    def test_invalid_field_content_assignment(self) -> None:
        """Raise ValidationError: assign a string to an int field."""
        id_ = 5
        base_class = StrictBaseClass(id=id_)
        self.assertEqual(base_class.id, id_)

        with self.assertRaises(pydantic.ValidationError):
            base_class.id = "invalid-value"  # type: ignore[assignment]

    def test_invalid_field_in_init(self) -> None:
        """Raise ValidationError: try to initialize a non-existing field."""
        with self.assertRaises(pydantic.ValidationError):
            StrictBaseClass(does_not_exist=20)  # type: ignore[call-arg]


class ModelToTest(pydantic.BaseModel):
    """Simple model to be used in a test."""

    id: int
    created_at: datetime


class ModelToDictTests(TestCase):
    """Tests for model_to_json_serializable_dict function."""

    def test_model_to_json_serializable_dict(self) -> None:
        """model_to_json_serializable_dict return datetime as str."""
        obj_id = 4
        created_at = datetime.now(tz=timezone.utc)

        model = ModelToTest(id=obj_id, created_at=created_at)
        expected = {"id": obj_id, "created_at": created_at.isoformat()}

        self.assertEqual(model_to_json_serializable_dict(model), expected)


class AssetResponseTest(TestCase):
    """Tests for the AssetResponse model."""

    def test_parse(self) -> None:
        """Test parsing a nested dictionary."""
        body = {
            "id": 99,
            "category": "debusine:signing-key",
            "workspace": "test",
            "data": {
                "purpose": "openpgp",
                "fingerprint": "ABC123",
                "public_key": "PUBLIC KEY",
                "description": "Description",
            },
            "work_request": 5,
        }
        AssetResponse.parse_obj(body)

    def test_parse_no_category(self) -> None:
        """Test parsing an asset without a category."""
        body = {
            "id": 99,
            "category": "",
            "workspace": "test",
            "data": {},
            "work_request": 5,
        }
        with self.assertRaisesRegex(pydantic.ValidationError, r".*category.*"):
            AssetResponse.parse_obj(body)


class AssetsResponsesTest(TestCase):
    """Tests for the AssetsResponses model."""

    def test_iter(self) -> None:
        """Test that AssetsResponses are iterable."""
        responses = AssetsResponse.parse_obj(
            [
                AssetResponse(
                    id=99,
                    category=AssetCategory.SIGNING_KEY,
                    workspace="test",
                    data=SigningKeyData(
                        purpose=KeyPurpose.OPENPGP,
                        fingerprint="ABC123",
                        public_key="PUBLIC KEY",
                        description="Description",
                    ),
                ).dict()
            ]
        )
        self.assertEqual(len(list(responses)), 1)


class EnrollPayloadTest(TestCase):
    """Test for :py:class:`EnrollPayload`."""

    def test_invalid_nonces(self) -> None:
        """Invalid nonces are caught."""
        for nonce in (
            # Too short
            "",
            "a",
            "a" * 7,
            # Too long
            "a" * 65,
            "a" * 128,
            # Path components
            "/foo/bar",
            "../foobar",
            # Query strings
            "?mischief",
            # Phrases
            "foo bar baz",
        ):
            with (
                self.subTest(nonce=nonce),
                self.assertRaisesRegex(ValueError, "Nonce is malformed"),
            ):
                EnrollPayload(
                    nonce=nonce,
                    challenge="correct horse battery staple",
                    hostname="hostname",
                    scope="scope",
                )

    def test_valid_nonces(self) -> None:
        """Acceptable nonces."""
        # The character set used by secrets.token_urlsafe should be accepted
        for nonce in (
            # Between 8 and 64 characters
            "a" * 8,
            "a" * 64,
            # Letters (upper and lowercase)
            "ABCDefgh",
            # Numbers
            "12345678",
            # Underscores and dashes
            "_-_-_-_-",
        ):
            with self.subTest(nonce=nonce):
                payload = EnrollPayload(
                    nonce=nonce,
                    challenge="correct horse battery staple",
                    hostname="hostnmame",
                    scope="scope",
                )
                self.assertEqual(payload.nonce, nonce)

    def test_invalid_challenges(self) -> None:
        """Invalid challenges are caught."""
        for challenge in (
            # Empty string
            "",
            # Blank string
            " " * 16,
            "\t" * 16,
            "\n" * 16,
            # Nonwords
            "...... ..... .....",
            # Short words
            "a b c d e",
            "foo bar baz",
            # At least 3 words
            "wibble",
            "wibble wobble",
            # Maximum 8 words
            "first second third fourth fifth sixth seventh eight ninth",
            # Words of up to 10 characters
            f"{'a' * 11} {'a' * 11} {'a' * 11}",
            # Non-ASCII letters
            "ÆØÅ àéíòùý",
            # HTML
            "<blink>Don't blink</blink>",
        ):
            with (
                self.subTest(challenge=challenge),
                self.assertRaisesRegex(ValueError, "Challenge is malformed"),
            ):
                EnrollPayload(
                    nonce="12345678",
                    challenge=challenge,
                    hostname="hostname",
                    scope="scope",
                )

    def test_valid_challenges(self) -> None:
        """Acceptable challenges."""
        for challenge in (
            # Words of at least 4 letters
            "ciao ciao ciao",
            # At least 3 words
            "wibble wabble wobble wubble",
            # Up to 8 words
            "first second third fourth fifth sixth seventh eight",
            # Words of up to 10 characters
            f"{'a' * 8} {'a' * 9} {'a' * 10}",
            # XKCD passwords
            "correct horse battery staple",
        ):
            with self.subTest(challenge=challenge):
                payload = EnrollPayload(
                    nonce="12345678",
                    challenge=challenge,
                    hostname="hostname",
                    scope="scope",
                )
                self.assertEqual(payload.challenge, challenge)


class EnrollConfirmPayloadTest(TestCase):
    """Test for :py:class:`EnrollConfirmPayload`."""

    def test_invalid_tokens(self) -> None:
        """Invalid tokens are caught."""
        for token in (
            # Too short
            "",
            "a",
            "a" * 7,
            # Too long
            "a" * 65,
            "a" * 128,
            # Path components
            "/foo/bar",
            "../foobar",
            # Query strings
            "?mischief",
            # Phrases
            "foo bar baz",
            # Blank string
            " " * 16,
            "\t" * 16,
            "\n" * 16,
            # Only letters and numbers
            "................",
            "wibble wobble",
            "ÆØÅ àéíòùý",
            "<blink>Don't blink</blink>",
        ):
            with (
                self.subTest(token=token),
                self.assertRaisesRegex(ValueError, "Token is malformed"),
            ):
                EnrollConfirmPayload(outcome=EnrollOutcome.CONFIRM, token=token)

    def test_valid_tokens(self) -> None:
        """Acceptable tokens."""
        for token in (
            # Output of token_hex
            secrets.token_hex(32),
            # Between 8 and 64 characters
            "a" * 8,
            "a" * 64,
            # Letters (upper and lowercase)
            "ABCDefgh",
            # Numbers
            "12345678",
        ):
            with self.subTest(token=token):
                payload = EnrollConfirmPayload(
                    outcome=EnrollOutcome.CONFIRM, token=token
                )
                self.assertEqual(payload.token, token)


class WorkspaceInheritanceChainElementTests(TestCase):
    """Test for :py:class:`WorkspaceInheritanceChainElement`."""

    def test_from_string(self) -> None:
        for value, expected in (
            ("42", WorkspaceInheritanceChainElement(id=42)),
            ("test", WorkspaceInheritanceChainElement(workspace="test")),
            (
                "foo/bar",
                WorkspaceInheritanceChainElement(scope="foo", workspace="bar"),
            ),
        ):
            with self.subTest(value=value):
                self.assertEqual(
                    WorkspaceInheritanceChainElement.from_string(value),
                    expected,
                )

    def test_matches(self) -> None:
        El = WorkspaceInheritanceChainElement
        sample = El(id=42, scope="scope", workspace="workspace")
        for a, b, expected in (
            (sample, El(id=42), True),
            (El(id=42, scope="scope", workspace="workspace"), El(id=43), False),
            (sample, El(workspace="workspace"), True),
            (sample, El(workspace="invalid"), False),
            (sample, El(id=42, scope="scope", workspace="workspace"), True),
            (sample, El(id=43, scope="scope", workspace="workspace"), False),
            (sample, El(id=42, scope="invalid", workspace="workspace"), False),
            (sample, El(id=42, scope="scope", workspace="invalid"), False),
        ):
            with self.subTest(a=a, b=b):
                self.assertEqual(a.matches(b), expected)

    def test_invalid(self) -> None:
        class Kwargs(TypedDict):
            id: NotRequired[int]
            scope: NotRequired[str]
            workspace: NotRequired[str]

        kwargs: Kwargs
        for kwargs in (Kwargs(), Kwargs(scope="scope")):
            with (
                self.subTest(kwargs=kwargs),
                self.assertRaisesRegex(
                    ValueError, r"at least id or workspace need to be set"
                ),
            ):
                WorkspaceInheritanceChainElement(**kwargs)


class WorkspaceInheritanceChainTests(TestCase):
    """Test for :py:class:`WorkspaceInheritanceChain`."""

    def test_empty(self) -> None:
        val = WorkspaceInheritanceChain()
        self.assertEqual(val.dict(), {"chain": []})

    def test_from_strings(self) -> None:
        El = WorkspaceInheritanceChainElement

        def chain(
            *args: WorkspaceInheritanceChainElement,
        ) -> WorkspaceInheritanceChain:
            return WorkspaceInheritanceChain(chain=list(args))

        for strings, expected in (
            ([], chain()),
            (["42"], chain(El(id=42))),
            (["test"], chain(El(workspace="test"))),
            (
                ["scope/workspace"],
                chain(El(scope="scope", workspace="workspace")),
            ),
            (
                ["1", "test", "scope/workspace"],
                chain(
                    El(id=1),
                    El(workspace="test"),
                    El(scope="scope", workspace="workspace"),
                ),
            ),
        ):
            with self.subTest(strings=strings):
                self.assertEqual(
                    WorkspaceInheritanceChain.from_strings(strings), expected
                )

    def test_add(self) -> None:
        El = WorkspaceInheritanceChainElement

        def chain(
            *args: WorkspaceInheritanceChainElement,
        ) -> WorkspaceInheritanceChain:
            return WorkspaceInheritanceChain(chain=list(args))

        el1 = El(id=1)
        el2 = El(id=2)
        for a, b, expected in (
            (chain(), chain(el1, el2), chain(el1, el2)),
            (chain(el1), chain(el2), chain(el1, el2)),
            (chain(el1, el2), chain(), chain(el1, el2)),
        ):
            self.assertEqual(a + b, expected)

    def test_sub(self) -> None:
        El = WorkspaceInheritanceChainElement

        def chain(
            *args: WorkspaceInheritanceChainElement,
        ) -> WorkspaceInheritanceChain:
            return WorkspaceInheritanceChain(chain=list(args))

        el1 = El(id=1)
        el2 = El(id=2)
        el3 = El(id=3)
        for a, b, expected in (
            (chain(), chain(el1, el2), chain()),
            (chain(el1, el2), chain(el2), chain(el1)),
            (chain(el1, el2, el3), chain(), chain(el1, el2, el3)),
            (chain(el1, el2, el3), chain(el1, el3), chain(el2)),
        ):
            with self.subTest(a=a, b=b):
                self.assertEqual(a - b, expected)
