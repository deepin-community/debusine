.. _artifact-system-image:

Category ``debian:system-image``
================================

This artifact contains a disk image of a Debian system. The disk has
a GPT partition table and is EFI bootable (for architectures that support
EFI). The disk image should usually contain an EFI partition and a
partition for the root filesystem. The root partition should use
a partition type UUID respecting the `discoverable partitions
specification
<https://uapi-group.org/specifications/specs/discoverable_partitions_specification/>`__.

* Data:

  * Same as ``debian:system-tarball`` with some extra fields. The
    ``filename`` field points to the disk image.
  * image_format: indicates the format of the image (e.g. ``raw``,
    ``qcow2``)
  * filesystem: indicates the filesystem used on the root filesystem (e.g.
    ``ext4``, ``btrfs``, ``iso9660``)
  * size: indicates the size of the filesystem on the root filesystem (in
    bytes)
  * boot_mechanism: a list of all the ways that the image can be booted.
    Valid values are ``efi`` and ``bios``.

* Files:

  * ``$filename`` (e.g. ``image.tar.xz``, ``image.qcow2``, ``image.iso``):
    the nature of the file depends on the ``image_format`` field.

* Relationships:

  * None.

.. note::
   At this point, we expect official Debusine tasks to only generate and
   use images that are bootable with EFI. But the artifact specification
   has the ``boot_mechanism`` key to be future-proof and for the benefit
   of custom tasks that would make different choices.

``raw`` image format
~~~~~~~~~~~~~~~~~~~~

The image itself is wrapped in a xz-compressed tarball to be able to
properly support sparse filesystem images (i.e. files with holes without
any data) and to save some space with compression.

The ``filename`` field points to the tarball that should contain a
``root.img`` file which is the raw disk image.

``qcow2`` image format
~~~~~~~~~~~~~~~~~~~~~~

The ``filename`` field points directly to the ``qcow2`` image.
