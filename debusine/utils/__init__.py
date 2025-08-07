# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""utils module contain utilities used by different components of debusine."""

import hashlib
import re
import shutil
from collections.abc import Callable, Generator, Mapping, Sequence
from enum import StrEnum
from pathlib import Path
from types import GenericAlias
from typing import (
    Any,
    Generic,
    Literal,
    TYPE_CHECKING,
    TypeVar,
    TypedDict,
    Union,
    get_args,
    get_origin,
)

from requests.structures import CaseInsensitiveDict

from debian import deb822

if TYPE_CHECKING:
    from django.http.request import HttpHeaders

CALCULATE_HASH_CHUNK_SIZE = 1 * 1024 * 1024


def calculate_hash(file_path: Path, hash_name: str) -> bytes:
    """Return hash (using algorithm hash_name) of file."""
    hasher = hashlib.new(hash_name)

    with open(file_path, "rb") as f:
        while chunk := f.read(CALCULATE_HASH_CHUNK_SIZE):
            hasher.update(chunk)

    return hasher.digest()


_Get = TypeVar("_Get", covariant=True)


# taken from functional.py in python3-django 3:4.2.11-1 to avoid the dependency
class classproperty(Generic[_Get]):
    """
    classproperty definition.

    Decorator that converts a method with a single cls argument into a property
    that can be accessed directly from the class.
    """

    def __init__(self, method: Callable[[Any], _Get]) -> None:
        """Define __init__."""
        self.fget = method

    def __get__(
        self, instance: Any | None, cls: type[Any] | None = None  # noqa: U100
    ) -> _Get:
        """Define __get__."""
        return self.fget(cls)


def _error_message_invalid_header(header_name: str, header_value: str) -> str:
    return f'Invalid {header_name} header: "{header_value}"'


def parse_range_header(
    headers: Union[CaseInsensitiveDict[str], "HttpHeaders"],
) -> dict[str, int] | None:
    """Parse headers["Range"]. Return dictionary with information."""
    header_name = "Range"
    header_value = headers.get(header_name)

    if header_value is None:
        return None

    if m := re.match("bytes=([0-9]+)-([0-9]+)", header_value):
        return {"start": int(m.group(1)), "end": int(m.group(2))}

    raise ValueError(_error_message_invalid_header(header_name, header_value))


class ParsedContentRange(TypedDict):
    """A parsed Content-Range header."""

    start: int | Literal["*"]
    end: int | None
    size: int | Literal["*"]


def parse_content_range_header(
    headers: Mapping[str, str],
) -> ParsedContentRange | None:
    """Parse headers["Content-Range"]. Return dictionary with information."""
    header_name = "Content-Range"
    header_value = headers.get("Content-Range")

    if header_value is None:
        return None

    if m := re.match(r"bytes ([0-9]+)-([0-9]+)/([0-9]+|\*)", header_value):
        return {
            "start": int(m.group(1)),
            "end": int(m.group(2)),
            "size": "*" if m.group(3) == "*" else int(m.group(3)),
        }
    elif m := re.match(r"bytes \*/([0-9]+)", header_value):
        return {
            "start": "*",
            "end": None,
            "size": int(m.group(1)),
        }
    elif re.match(r"bytes \*/\*", header_value):
        return {
            "start": "*",
            "end": None,
            "size": "*",
        }
    raise ValueError(_error_message_invalid_header(header_name, header_value))


def read_dsc(dsc_path: Path | None) -> deb822.Dsc | None:
    """
    If dsc_path is not None: read the file and return the contents.

    If the dsc does not have at least "source" and "version" return None.
    """
    if dsc_path is None:
        return None

    with open(dsc_path) as dsc_file:
        dsc = deb822.Dsc(dsc_file)

        if "source" in dsc and "version" in dsc:
            # At least "source" and "version" must exist to be a valid
            # dsc file in the context of Sbuild task.
            return dsc

    return None


def read_changes(build_directory: Path) -> deb822.Changes | None:
    """
    Find the file .changes in build_directory, read and return it.

    If the changes file does not exist, return None.
    """
    changes_path = find_file_suffixes(build_directory, [".changes"])

    if changes_path is None:
        return None

    with open(changes_path) as changes_file:
        changes = deb822.Changes(changes_file)
        return changes


def find_files_suffixes(
    directory: Path, endswith: Sequence[str], *, include_symlinks: bool = False
) -> list[Path]:
    """
    Return files (sorted) ending with any of the endswith in directory.

    :param directory: directory where to search the files
    :param endswith: suffix to return the files from the directory
    :param include_symlinks: if False (default): does not return symbolic links,
      if True return symbolic links

    Find only regular files (no symbolic links, directories, etc.).
    """
    found_files: list[Path] = []

    for file in directory.iterdir():
        if not include_symlinks:
            if file.is_symlink():
                continue

        if not file.is_file():
            continue

        if file.name.endswith(tuple(endswith)):
            found_files.append(file)

    return sorted(found_files)


def find_file_suffixes(directory: Path, endswith: Sequence[str]) -> Path | None:
    """
    Find and return file ending with any of the endswith in directory.

    Finds regular files (no symbolic links, directories, etc.).

    Raise RuntimeError if more than one file could be returned.
    """
    found_files = find_files_suffixes(directory, endswith)

    number_of_files = len(found_files)

    if number_of_files == 1:
        return found_files[0]
    elif number_of_files == 0:
        return None
    else:
        found_files.sort()
        found_paths = [str(f) for f in found_files]
        raise RuntimeError(f"More than one {endswith} file: {found_paths}")


def is_command_available(cmd: str) -> bool:
    """
    Check whether cmd is available on $PATH.

    :param cmd: command name to check, passed to shutil.which.  (This may be
      a full path name, in which case shutil.which simply checks whether an
      executable exists at that path.)
    """
    return shutil.which(cmd) is not None


# The return type is really typing._GenericAlias, but that's private.
def _get_specialization(cls: type, base: type) -> Any:
    """
    Inspect a class for its specialization of a generic base class.

    A class may specialize a generic base class in various ways: for
    example, it might supply specific values for some of the relevant type
    variables, or it might itself be generic and rely on being specialized
    by its own subclasses, or both.  To help us introspect such classes,
    this returns the specialization of `base` by `cls` in the form of a
    :py:class:`typing._GenericAlias`.

    :raises AssertionError: if `cls` specializes `base` in different ways by
      means of multiple inheritance.
    """
    specializations: set[GenericAlias] = set()
    for cls_base in getattr(cls, "__orig_bases__", []):
        origin = get_origin(cls_base)
        if origin == base:
            specializations.add(cls_base)
        elif origin is not None and issubclass(origin, base):
            specializations.add(
                _get_specialization(origin, base)[get_args(cls_base)]
            )
    if len(specializations) != 1:
        raise AssertionError(
            f"{cls.__qualname__} must specialize {base.__qualname__} with "
            f"exactly one consistent list of type arguments"
        )
    [specialization] = specializations
    return specialization


def extract_generic_type_arguments(
    cls: type, expected_origin: type
) -> tuple[type, ...]:
    """
    Extract type arguments from a generic class.

    This is expected to be called from __init_subclass__ in a generic class
    (i.e. one that has Generic[...] as a base class), and allows extracting
    the specializing type arguments so that they can be used as factories.
    """
    return get_args(_get_specialization(cls, expected_origin))


class DjangoChoicesEnum(StrEnum):
    """Enable a StrEnum to be used for a Django choices field."""

    @classproperty
    def choices(cls) -> Generator[tuple[str, str]]:
        """Return a Django model compatible set of choices."""
        for item in cls:
            yield (item, item)


class NotSupportedError(Exception):
    """The requested function is not supported."""

    pass
