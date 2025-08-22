# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""A custom Sphinx domain for Debusine-specific items."""

from collections.abc import Generator
from typing import Any

from docutils import nodes
from sphinx import addnodes
from sphinx.application import Sphinx
from sphinx.builders import Builder
from sphinx.directives import ObjectDescription
from sphinx.domains import Domain, ObjType
from sphinx.environment import BuildEnvironment
from sphinx.roles import XRefRole
from sphinx.util import logging
from sphinx.util.nodes import make_refnode

logger = logging.getLogger(__name__)


class BuildingBlockDirective(ObjectDescription[str]):
    """A custom directive describing a Debusine building block."""

    has_content = True
    required_arguments = 1

    block_type: str
    block_description: str

    def handle_signature(
        self, sig: str, signode: addnodes.desc_signature  # noqa: U100
    ) -> str:
        """Parse a signature and return a value that identifies the object."""
        signode += addnodes.desc_name(text=sig)
        return sig

    def add_target_and_index(
        self, name: str, sig: str, signode: addnodes.desc_signature
    ) -> None:
        """Add cross-reference IDs and entries to the index."""
        node_id = f"{self.block_type}-{name}"
        signode["ids"].append(node_id)
        self.indexnode["entries"].append(
            ("single", f"{name} ({self.block_description})", node_id, "", None)
        )
        domain = self.env.get_domain("debusine")
        assert isinstance(domain, DebusineDomain)
        domain.add_building_block(self.block_type, sig)


class ArtifactDirective(BuildingBlockDirective):
    """A custom directive describing an artifact."""

    block_type = "artifact"
    block_description = "artifact category"


class AssetDirective(BuildingBlockDirective):
    """A custom directive describing an asset."""

    block_type = "asset"
    block_description = "asset category"


class BareDataDirective(BuildingBlockDirective):
    """A custom directive describing bare data."""

    block_type = "bare-data"
    block_description = "bare data category"


class CollectionDirective(BuildingBlockDirective):
    """A custom directive describing a collection."""

    block_type = "collection"
    block_description = "collection category"


class FileBackendDirective(BuildingBlockDirective):
    """A custom directive describing a file backend."""

    block_type = "file-backend"
    block_description = "file backend"


class TaskDirective(BuildingBlockDirective):
    """A custom directive describing a task."""

    block_type = "task"
    block_description = "task name"


class WorkflowDirective(BuildingBlockDirective):
    """A custom directive describing a workflow."""

    block_type = "workflow"
    block_description = "workflow name"


class DebusineDomain(Domain):
    """A domain containing Debusine building blocks."""

    name = "debusine"
    label = "Debusine"
    object_types = {
        "artifact": ObjType("artifact", "artifact"),
        "asset": ObjType("asset", "asset"),
        "bare-data": ObjType("bare data", "bare-data"),
        "collection": ObjType("collection", "collection"),
        "file-backend": ObjType("file backend", "file-backend"),
        "task": ObjType("task", "task"),
        "workflow": ObjType("workflow", "workflow"),
    }
    roles = {
        "artifact": XRefRole(innernodeclass=nodes.inline),
        "asset": XRefRole(innernodeclass=nodes.inline),
        "bare-data": XRefRole(innernodeclass=nodes.inline),
        "collection": XRefRole(innernodeclass=nodes.inline),
        "file-backend": XRefRole(innernodeclass=nodes.inline),
        "task": XRefRole(innernodeclass=nodes.inline),
        "workflow": XRefRole(innernodeclass=nodes.inline),
    }
    directives = {
        "artifact": ArtifactDirective,
        "asset": AssetDirective,
        "bare-data": BareDataDirective,
        "collection": CollectionDirective,
        "file-backend": FileBackendDirective,
        "task": TaskDirective,
        "workflow": WorkflowDirective,
    }
    initial_data = {
        "objects": {},
    }

    def get_objects(self) -> Generator[tuple[str, str, str, str, str, int]]:
        """Return an iterable of object descriptions."""
        for (typ, name), (docname, node_id) in self.data["objects"].items():
            yield f"{typ}.{name}", name, typ, docname, node_id, 1

    def resolve_xref(
        self,
        env: BuildEnvironment,  # noqa: U100
        fromdocname: str,
        builder: Builder,
        typ: str,
        target: str,
        node: addnodes.pending_xref,
        contnode: nodes.Element,
    ) -> nodes.reference | None:
        """Resolve a pending cross-reference with the given type and target."""
        objtypes = self.objtypes_for_role(typ)
        if not objtypes:
            return None
        for objtype in objtypes:
            result = self.data["objects"].get((objtype, target))
            if result:
                todocname, node_id = result
                return make_refnode(
                    builder,
                    fromdocname,
                    todocname,
                    node_id,
                    contnode,
                    f"{target} {objtype}",
                )
        logger.warning(
            "undefined %s: %r",
            typ,
            target,
            location=node,
            type="ref",
            subtype=node["reftype"],
        )
        return None

    def add_building_block(self, block_type: str, signature: str) -> None:
        """Add a new building block to the domain."""
        self.data["objects"][(block_type, signature)] = (
            self.env.docname,
            f"{block_type}-{signature}",
        )


def setup(app: Sphinx) -> dict[str, Any]:
    """Set up the extension."""
    app.add_domain(DebusineDomain)

    return {
        "parallel_read_safe": True,
        "parallel_write_safe": True,
    }
