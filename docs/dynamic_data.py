# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.
"""Extract data from docstrings and format it as RST."""


import inspect
import re

from docutils import nodes
from docutils.nodes import Node
from docutils.parsers.rst import Directive
from sphinx.ext.autodoc.importer import import_object
from sphinx.util.typing import OptionSpec


class DynamicDataDirective(Directive):
    """
    Sphinx directive to document dynamic data.

    To use, in an .rst file:

    .. code-block:: rst

      .. dynamic_data::
         :method: debusine.tasks.sbuild::Sbuild.build_dynamic_data

    In the Sbuild.build_dynamic_data method include in its docstring:

    .. code-block:: rst

        Computes the dynamic data for the task resolving artifacts.

        :subject: source package that is building
        :runtime_context: the context
    """

    required_arguments = 0
    optional_arguments = 0
    final_argument_whitespace = False

    option_spec: OptionSpec = {
        "method": str,
    }

    def run(self) -> list[Node]:
        """Run the dynamic data directive."""
        method_path = self.options.get("method")
        if not method_path or "::" not in method_path:
            return [
                self.state_machine.reporter.error(
                    '"dynamic_data" directive requires "method" option '
                    'in the format of "method: module_path::Class.method"',
                    line=self.lineno,
                )
            ]

        module_name, obj_path = method_path.split("::", 1)

        try:
            _, _, _, method = import_object(
                module_name, obj_path.split("."), objtype="method"
            )
        except ModuleNotFoundError:
            return [
                self.state_machine.reporter.error(
                    f'Cannot import "{method_path}". '
                    f'Expected "module::Class.method"',
                    line=self.lineno,
                )
            ]

        docstring = inspect.getdoc(method)

        if docstring is None:
            return [
                self.state_machine.reporter.error(
                    f"No docstring found in '{method_path}'",
                    line=self.lineno,
                )
            ]

        parsed = self._extract_dynamic_data_fields(docstring)

        if len(parsed) == 0:
            return [
                self.state_machine.reporter.error(
                    'No "Dynamic Data:" block or field keys found in '
                    f'"{method_path}"',
                    line=self.lineno,
                )
            ]

        return self._generate_dynamic_data_rst(parsed)

    @staticmethod
    def _extract_dynamic_data_fields(docstring: str) -> dict[str, str]:
        """
        Extract dynamic data fields from a docstring using field lists.

        :return: dictionary with dynamic data field names and descriptions.
        """
        dynamic_data = {}
        lines = docstring.splitlines()

        field_indentation_level = 0
        current_key: str | None = None

        for line in lines:
            stripped = line.strip()

            current_indentation_level = len(line) - len(line.lstrip(" "))

            if (
                current_key is not None
                and current_indentation_level > field_indentation_level
            ):
                dynamic_data[current_key] += f" {stripped}"
                continue

            match = re.match(r":(\w+):\s*(.*)", stripped)
            if match:
                key, value = match.groups()
                if key in ["returns", "return"]:
                    # Skip "returns" / "return" which is likely to be what the
                    # method return, not a configuration key
                    continue

                current_key = key
                dynamic_data[current_key] = value
                field_indentation_level = current_indentation_level

        return dynamic_data

    def _generate_dynamic_data_rst(
        self, dynamic_data: dict[str, str]
    ) -> list[nodes.bullet_list]:
        """Generate a Sphinx field list from extracted dynamic data."""
        bullet_list = nodes.bullet_list()

        for key, value in dynamic_data.items():
            contents = nodes.paragraph()

            contents += nodes.strong(text=f"{key}: ")

            inline_nodes, _ = self.state.inline_text(value, self.lineno)
            contents += inline_nodes

            list_item = nodes.list_item()
            list_item += contents

            bullet_list.append(list_item)

        return [bullet_list]
