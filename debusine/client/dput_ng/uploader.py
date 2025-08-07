# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Debusine dput-ng uploader implementation."""

from pathlib import Path
from typing import Any
from urllib.parse import quote, urljoin

from dput.core import logger
from dput.exceptions import UploadException
from dput.uploader import AbstractUploader

from debusine.client.client_utils import prepare_changes_for_upload
from debusine.client.dput_ng.dput_ng_utils import make_debusine_client
from debusine.client.models import RelationType


class DebusineUploader(AbstractUploader):  # type: ignore[misc]
    """An interface to upload files to debusine."""

    def initialize(self, **kwargs: Any) -> None:
        """See :py:meth:`AbstractUploader.initialize`."""
        self.scope = self._config["debusine_scope"]
        self.workspace = self._config["debusine_workspace"]
        self.debusine = make_debusine_client(self._config)
        self.paths: list[Path] = []

    def upload_file(
        self, filename: str, upload_filename: str | None = None
    ) -> None:
        """See :py:meth:`AbstractUploader.upload_file`."""
        if upload_filename is not None:
            raise NotImplementedError(
                "Debusine does not support commands files."
            )

        path = Path(filename)
        for old_path in self.paths:
            if old_path.suffix == path.suffix and path.suffix == ".changes":
                raise RuntimeError("Cannot upload multiple .changes files")

        self.paths.append(path)

        if path.suffix == ".changes":
            # TODO: We should check that the files expected by these
            # artifacts match the set of files referenced by self.paths.
            artifacts = prepare_changes_for_upload(path)
            try:
                uploaded = [
                    self.debusine.upload_artifact(
                        artifact, workspace=self.workspace
                    )
                    for artifact in artifacts
                ]
                primary_artifact = uploaded[0]
                for related_artifact in uploaded[1:]:
                    for relation_type in (
                        RelationType.EXTENDS,
                        RelationType.RELATES_TO,
                    ):
                        self.debusine.relation_create(
                            primary_artifact.id,
                            related_artifact.id,
                            relation_type,
                        )
            except Exception as e:
                raise UploadException(str(e)) from e
            logger.info(
                "Created artifact: %s",
                # TODO: Ideally we wouldn't have to hardcode the URL
                # structure here.
                urljoin(
                    self.debusine.base_api_url,
                    f"/{quote(self.scope)}/{quote(self.workspace)}/artifact/"
                    f"{primary_artifact.id}/",
                ),
            )

            # Save artifact IDs in the in-memory copy of the profile, for
            # use by hooks.
            artifact_ids = {"changes": primary_artifact.id}
            self._config["debusine_artifact_ids"] = artifact_ids
