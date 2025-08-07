# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Task to use lintian in debusine."""
import json
import re
from pathlib import Path
from typing import Any

from debian import debfile
from debusine import utils
from debusine.artifacts import LintianArtifact
from debusine.artifacts.models import (
    ArtifactCategory,
    CollectionCategory,
    DebianBinaryPackage,
    DebianBinaryPackages,
    DebianLintianSummary,
    DebianSourcePackage,
    DebianSystemTarball,
    DebianUpload,
    get_source_package_name,
)
from debusine.client.models import RelationType
from debusine.tasks import BaseTaskWithExecutor, RunCommandTask
from debusine.tasks.models import (
    LintianData,
    LintianDynamicData,
    LintianPackageType,
)
from debusine.tasks.server import TaskDatabaseInterface


class Lintian(
    RunCommandTask[LintianData, LintianDynamicData],
    BaseTaskWithExecutor[LintianData, LintianDynamicData],
):
    """Task to use lintian in debusine."""

    TASK_VERSION = 1

    # Keep _SEVERITY_SHORT_TO_LONG ordered from higher to lower
    _SEVERITY_SHORT_TO_LONG = {
        "E": "error",
        "W": "warning",
        "I": "info",
        "P": "pedantic",
        "X": "experimental",
        "O": "overridden",
        "C": "classification",  # ignored for the fail_on_severity
        "M": "masked",  # In _IGNORED_SEVERITIES: not exposed to the user
    }

    # Severities that are not exposed to the user
    _IGNORED_SEVERITIES = {"masked"}

    CAPTURE_OUTPUT_FILENAME = "lintian.txt"

    def __init__(
        self,
        task_data: dict[str, Any],
        dynamic_task_data: dict[str, Any] | None = None,
    ) -> None:
        """Initialize object."""
        super().__init__(task_data, dynamic_task_data)

        # In the lintian cmd: the target that is tested (local file, .dsc
        # or .changes)
        self._lintian_targets: list[Path] | None = None

        # Package type to package names that are being analysed by
        # this Lintian invocation. In other words: packages belonging
        # to each package type.
        self._package_type_to_packages: dict[LintianPackageType, set[str]] = {
            LintianPackageType.SOURCE: set(),
            LintianPackageType.BINARY_ANY: set(),
            LintianPackageType.BINARY_ALL: set(),
        }

        self._package_name_to_filename: dict[str, str] = {}

        self._severities_to_level = {}  # dictionary with severity's name to
        # level. Higher is bigger severity
        for ranking, severity in enumerate(
            reversed(self._SEVERITY_SHORT_TO_LONG.values())
        ):
            self._severities_to_level[severity] = ranking

    def build_dynamic_data(
        self, task_database: TaskDatabaseInterface
    ) -> LintianDynamicData:
        """
        Resolve artifact lookups for this task.

        :subject: name of source package of ``input.source_artifact`` or
          ``input.binary_artifacts``
        :runtime_context: ``what:distribution`` where ``what`` is what
          got analyzed (``source``, ``binary-all`` or ``binary-any``) and
          ``distribution`` is ``codename`` of ``environment``.
        :configuration_context: ``codename`` of ``environment``
        """
        input_source_artifact = task_database.lookup_single_artifact(
            self.data.input.source_artifact
        )
        input_binary_artifacts = task_database.lookup_multiple_artifacts(
            self.data.input.binary_artifacts
        )

        subject = None
        if input_source_artifact is not None:
            self.ensure_artifact_categories(
                configuration_key="input.source_artifact",
                category=input_source_artifact.category,
                expected=(
                    ArtifactCategory.SOURCE_PACKAGE,
                    ArtifactCategory.UPLOAD,
                ),
            )
            assert isinstance(
                input_source_artifact.data, (DebianSourcePackage, DebianUpload)
            )
            subject = get_source_package_name(input_source_artifact.data)

        # Validate input_binary_artifacts and get subject
        if input_binary_artifacts:
            for binary_artifact in input_binary_artifacts:
                self.ensure_artifact_categories(
                    configuration_key="input.binary_artifact",
                    category=binary_artifact.category,
                    expected=(
                        ArtifactCategory.BINARY_PACKAGE,
                        ArtifactCategory.BINARY_PACKAGES,
                        ArtifactCategory.UPLOAD,
                    ),
                )
                assert isinstance(
                    binary_artifact.data,
                    (DebianBinaryPackage, DebianBinaryPackages, DebianUpload),
                )

                if subject is None:
                    subject = get_source_package_name(binary_artifact.data)

        what = {"source", "binary-all", "binary-any"}

        output = self.data.output
        if not output.source_analysis:
            what.remove("source")
        if not output.binary_all_analysis:
            what.remove("binary-all")
        if not output.binary_any_analysis:
            what.remove("binary-any")

        what_str = "+".join(sorted(what))

        runtime_context = None
        configuration_context = None
        environment_id = None
        if self.data.environment is not None:
            environment = self.get_environment(
                task_database,
                self.data.environment,
                default_category=CollectionCategory.ENVIRONMENTS,
            )
            environment_id = environment.id

            self.ensure_artifact_categories(
                configuration_key="environment",
                category=environment.category,
                expected=(ArtifactCategory.SYSTEM_TARBALL,),
            )
            assert isinstance(environment.data, DebianSystemTarball)

            runtime_context = f"{what_str}:{environment.data.codename}"
            configuration_context = environment.data.codename

        return LintianDynamicData(
            environment_id=environment_id,
            input_source_artifact_id=(
                None
                if input_source_artifact is None
                else input_source_artifact.id
            ),
            input_binary_artifacts_ids=input_binary_artifacts.get_ids(),
            subject=subject,
            runtime_context=runtime_context,
            configuration_context=configuration_context,
        )

    @classmethod
    def generate_severity_count_zero(cls) -> dict[str, int]:
        """Return dictionary. Keys: severities. Value: 0."""
        return {
            value: 0
            for value in cls._SEVERITY_SHORT_TO_LONG.values()
            if value not in cls._IGNORED_SEVERITIES
        }

    def _create_analysis(
        self,
        parsed_output: list[dict[str, str]],
        packages: set[str],
    ) -> dict[str, Any]:
        """
        Parse output of lintian, return the analysis.

        :param parsed_output: output of lintian (parsed by _parse_output())
        :param packages: package names to consider for this analysis
        :return: Dictionary with "tags" key and the list of tags (sorted by
          package, severity, tag and tag_information)
        """
        # Create a copy of each tag, remove " source" from the package name
        # (analysis.json does not have " source", but "parsed_output" is
        # the output from `lintian` and has it

        # Create dictionary with all severities and 0 in the value
        tags_count_by_severity = self.generate_severity_count_zero()

        tags_found = set()
        overridden_tags_found = set()
        package_filename = {}
        tags_for_packages = []

        for tag in parsed_output:
            package = tag["package"]

            if package not in packages:
                continue

            package_no_source_suffix = package.removesuffix(" source")
            package_filename[package_no_source_suffix] = (
                self._package_name_to_filename[package]
            )

            tags_for_packages.append(
                {**tag, "package": package_no_source_suffix}
            )

            tag_name = tag["tag"]

            tags_count_by_severity[tag["severity"]] += 1

            if tag["severity"] == "overridden":
                overridden_tags_found.add(tag_name)
            else:
                tags_found.add(tag_name)

        summary = {
            "tags_count_by_severity": tags_count_by_severity,
            "package_filename": package_filename,
            "tags_found": sorted(tags_found),
            "overridden_tags_found": sorted(overridden_tags_found),
            "lintian_version": self._get_lintian_version(),
            "distribution": self.data.target_distribution,
        }

        return {
            "tags": tags_for_packages,
            "summary": summary,
            "version": self.TASK_VERSION,
        }

    @classmethod
    def _parse_tag_line(cls, line: str) -> dict[str, str] | None:
        """
        Return dictionary with the tag information or None.

        Lintian in Debian bullseye return:
        W: hello source: package-uses-deprecated-debhelper-compat-version 9
        N:
        W: package-uses-deprecated-debhelper-compat-version

        The second "W:" line is a tag_line but _parse_tag_line() return
        None: the information is already included. This line can be ignored.
        """
        severities = "".join(cls._SEVERITY_SHORT_TO_LONG)

        m = re.match(
            fr"(?P<severity>[{severities}M]):\s(?P<package>\S+"
            fr"(?: source)?):\s(?P<tag>\S+) ?(?P<note>[^[]*)?",
            line,
        )
        # severity, package and tags are always there. Information might
        # or might not be there in line. If it's there is does NOT start
        # with [ (if it starts with [ it's a file, dealt below)

        if m is None:
            m = re.match(fr"(?P<severity>[{severities}M]):\s(?P<tag>\S+)", line)
            if m:
                # Bullseye's lintian have a duplicated tag line after each
                # real (full information). Ignore it.
                # Example:
                #
                # W: hello source: package-uses-deprecated-debhelper-compat-v 9
                # N:
                # W: package-uses-deprecated-debhelper-compat-v
                #
                # The second W: line is ignored.
                return None

            raise ValueError(f"Failed to parse line: {line}")

        parsed_tag = {
            "severity": cls._SEVERITY_SHORT_TO_LONG[m.group("severity")],
            "package": m.group("package"),
            "tag": m.group("tag"),
            "explanation": "",
            "comment": "",
            "note": (m.group("note") or "").strip(),
            "pointer": "",
        }

        m = re.match(r".*\[(?P<pointer>.*)]", line)

        if m:
            parsed_tag["pointer"] = m.group("pointer")

        return parsed_tag

    @classmethod
    def _parse_output(cls, lintian_output: Path) -> list[dict[str, str]]:
        """
        Parse output of lintian, return structured representation.

        :param lintian_output: file with lintian's output
        :return: list with a dictionary per each lintian line. Keys:
            severity, package, tag, details (if it's in the output), file
            (if it's in the output)
        """
        tags = []

        tag: dict[str, str] | None = None
        previous_line: str | None = None

        in_comment = False
        explanation: str = ""
        comment: str = ""

        with lintian_output.open() as output:
            for line in output:
                line = line.strip()
                if line == "N:" and previous_line == "N:":
                    # Comments (written by the maintainer in the lintian's
                    # override files) and explanations (all start by N:).
                    # The comments starts when there are "N:\nN:\n" in the
                    # output.
                    in_comment = True

                elif line.startswith("N:"):
                    parsed_line = line.lstrip("N:").lstrip() + "\n"
                    if in_comment:
                        comment += parsed_line
                    elif explanation != "" or parsed_line != "\n":
                        explanation += parsed_line

                else:
                    # Line not starting with "N:". It is a new tag.

                    # Add the explanation into the current tag before
                    # creating the new one
                    if tag:
                        tag["explanation"] = explanation.strip()
                        explanation = ""

                    tag = cls._parse_tag_line(line)

                    if tag is None:
                        continue

                    # If a comment was read (above the tag) add it
                    tag["comment"] = comment.strip()
                    comment = ""
                    # If we were in a comment: not anymore
                    in_comment = False

                    # Add the tag in the list, unless it is needed
                    # to be ignored (e.g. "masked")
                    if tag["severity"] not in cls._IGNORED_SEVERITIES:
                        tags.append(tag)

                previous_line = line

            # Add explanation for the last tag (usually "explanation"
            # is added when finding a new tag)
            if tag is not None:
                tag["explanation"] = explanation.strip()

        return tags

    def _cmdline(self) -> list[str]:
        """
        Build the lintian command line.

        Use configuration of self.data and self._lintian_targets.
        """
        if self.data.target_distribution == "jessie":
            display_level = "pedantic"
        else:
            display_level = "classification"

        cmd = [
            "lintian",
            "--no-cfg",
            "--display-level",
            f">={display_level}",
            "--display-experimental",
            "--info",
            "--show-overrides",
        ]

        if include_tags := self.data.include_tags:
            cmd.append("--tags=" + ",".join(include_tags))

        if exclude_tags := self.data.exclude_tags:
            cmd.append("--suppress-tags=" + ",".join(exclude_tags))

        # Set by configure_for_execution, which is always run before this.
        assert self._lintian_targets is not None

        cmd.extend([str(target) for target in self._lintian_targets])

        return cmd

    def _extract_package_name_type(
        self,
        file: Path,
    ) -> tuple[str | None, LintianPackageType | None]:
        """
        Return package name for file.

        :param file: if file ends in .deb or .udeb: use debfile.DebFile
            to return control["Package"]. If it's a .dsc: return
            dsc["Source"] + source. For other file extensions: return None.
        :return: package filename.
        """
        if file.suffix in (".deb", ".udeb"):
            pkg = debfile.DebFile(file)
            control = pkg.control.debcontrol()
            package_name = control["Package"]

            if control["Architecture"] == "all":
                package_type = LintianPackageType.BINARY_ALL
            else:
                package_type = LintianPackageType.BINARY_ANY

        elif file.suffix in ".dsc":
            dsc = utils.read_dsc(file)

            if dsc is None:
                self.append_to_log_file(
                    "configure_for_execution.log",
                    [f"{file} is not a valid .dsc file"],
                )
                return None, None

            # Lintian identifies the binary package Vs. source package with
            # the string " source". This is removed when presented to the user
            package_name = dsc["Source"] + " source"
            package_type = LintianPackageType.SOURCE
        else:
            package_name = None
            package_type = None

        return package_name, package_type

    def fetch_input(self, destination: Path) -> bool:
        """Download the required artifacts."""
        assert self.dynamic_data

        if (
            artifact_id := self.dynamic_data.input_source_artifact_id
        ) is not None:
            self.fetch_artifact(artifact_id, destination)

        for artifact_id in self.dynamic_data.input_binary_artifacts_ids:
            self.fetch_artifact(artifact_id, destination)

        return True

    def configure_for_execution(self, download_directory: Path) -> bool:
        """
        Find a .dsc, .deb and .udeb files in download_directory.

        Set self._lintian_targets to the relevant files.

        :param download_directory: where to search the files
        :return: True if valid files were found
        """
        self._prepare_executor_instance()

        if self.executor_instance is None:
            raise AssertionError("self.executor_instance cannot be None")

        self.run_executor_command(
            ["apt-get", "update"],
            log_filename="install.log",
            run_as_root=True,
            check=True,
        )
        self.run_executor_command(
            ["apt-get", "--yes", "install", "lintian"],
            log_filename="install.log",
            run_as_root=True,
            check=True,
        )

        self._lintian_targets = utils.find_files_suffixes(
            download_directory, [".dsc"]
        ) + utils.find_files_suffixes(download_directory, [".deb", ".udeb"])

        # Prepare mapping from filename to package name
        for file in download_directory.iterdir():
            package_name, arch = self._extract_package_name_type(
                download_directory / file
            )
            if package_name is None or arch is None:
                continue

            self._package_name_to_filename[package_name] = file.name
            self._package_type_to_packages[arch].add(package_name)

        if len(self._lintian_targets) == 0:
            ignored_file_names = [
                file.name for file in download_directory.iterdir()
            ]
            ignored_file_names.sort()

            self.append_to_log_file(
                "configure_for_execution.log",
                [
                    f"No *.dsc, *.deb or *.udeb to be analyzed. "
                    f"Files: {ignored_file_names}"
                ],
            )
            return False

        return True

    def execution_consistency_errors(
        self, build_directory: Path  # noqa: U100
    ) -> list[str]:
        """Return list of errors."""
        files_in_directory: set[Path] = set(build_directory.iterdir())

        if (
            build_directory / self.CAPTURE_OUTPUT_FILENAME
            not in files_in_directory
        ):
            return [f"{self.CAPTURE_OUTPUT_FILENAME} not in {build_directory}"]

        return []

    def upload_artifacts(
        self, exec_directory: Path, *, execution_success: bool  # noqa: U100
    ) -> None:
        """Upload the LintianArtifact with the files and relationships."""
        if not self.debusine:
            raise AssertionError("self.debusine not set")

        lintian_file = exec_directory / self.CAPTURE_OUTPUT_FILENAME

        package_type_to_analysis = self._create_package_type_to_analysis(
            lintian_file
        )

        for artifact_type, analysis in package_type_to_analysis.items():
            (
                analysis_file := exec_directory
                / f"analysis-{artifact_type}.json"
            ).write_text(json.dumps(analysis))

            lintian_artifact = LintianArtifact.create(
                analysis=analysis_file,
                lintian_output=lintian_file,
                summary=DebianLintianSummary(**analysis["summary"]),
            )

            uploaded = self.debusine.upload_artifact(
                lintian_artifact,
                workspace=self.workspace_name,
                work_request=self.work_request_id,
            )

            for source_artifact_id in self._source_artifacts_ids:
                self.debusine.relation_create(
                    uploaded.id, source_artifact_id, RelationType.RELATES_TO
                )

    def _create_package_type_to_analysis(
        self, lintian_file: Path
    ) -> dict[LintianPackageType, dict[str, Any]]:
        """
        Read lintian_file, return dict with LintianPackageType to analysis.

        :param lintian_file: Lintian output file
        :return: dict[LintianPackageType, Analysis]:
          only for the LintianPackageTypes in self.data["output"]
        """
        parsed_output = self._parse_output(lintian_file)

        parsed_output.sort(
            key=lambda x: (
                x["package"],
                -self._severities_to_level[
                    x["severity"]
                ],  # negative to sort from highest to lowest
                x["tag"],
                x["note"],
            )
        )

        package_analysis_mapping = {
            LintianPackageType.SOURCE: "source_analysis",
            LintianPackageType.BINARY_ALL: "binary_all_analysis",
            LintianPackageType.BINARY_ANY: "binary_any_analysis",
        }
        output = self.data.output

        package_type_to_analysis: dict[LintianPackageType, dict[str, Any]] = {}

        for package_type in LintianPackageType:
            if not getattr(output, package_analysis_mapping[package_type]):
                # self.data["output"]["source_analysis"] (or
                # "binary_all_analysis", or "binary_any_analysis") was disabled
                continue

            package_type_to_analysis[package_type] = self._create_analysis(
                parsed_output, self._package_type_to_packages[package_type]
            )

        return package_type_to_analysis

    def task_succeeded(
        self, returncode: int | None, execute_directory: Path  # noqa: U100
    ) -> bool:
        """
        Evaluate task output and return success.

        For a successful run of lintian:
        -lintian must have generated the output file
        -no tags of severity self.data["fail_on"] or higher

        :return: True for success, False failure.
        """
        fail_on_severity = self.data.fail_on_severity

        if fail_on_severity == "none":
            return True

        fail_on_severity_level = self._severities_to_level[fail_on_severity]
        lintian_file = execute_directory / self.CAPTURE_OUTPUT_FILENAME
        parsed_output = self._parse_output(lintian_file)

        for tag in parsed_output:
            if (
                self._severities_to_level[tag["severity"]]
                >= fail_on_severity_level
            ):
                return False

        return True

    def _get_lintian_version(self) -> str:
        if not self.executor_instance:
            raise AssertionError(
                "Task.executor_instance must be set before calling "
                "_get_lintian_version()"
            )

        out = self.executor_instance.run(
            ["lintian", "--print-version"],
            encoding="utf-8",
            errors="ignore",
            text=True,
            check=True,
        ).stdout

        return out.strip()

    def get_label(self) -> str:
        """Return the task label."""
        return (
            f"lintian {subject}"
            if (subject := self.get_subject())
            else "lintian"
        )
