.. _debusine-cli-config:

======================================
The debusine client configuration file
======================================

This is the reference documentation for the configuration file of the
:ref:`debusine <debusine-cli>` client command, which is read from
``~/.config/debusine/client/config.ini``.

The command follows the `ini file syntax <https://en.wikipedia.org/wiki/INI_file>`_
as understood by Python's `configparser <https://docs.python.org/3/library/configparser.html>`_.

This is an example configuration file::

    [General]
    default-server = debusine.debian.net

    [server:debusine.debian.net]
    api-url = https://debusine.debian.net/api
    scope = debian
    token = 12345678901234567890


``[General]`` section
=====================

The **General** section contains global settings for the client command.

The only global setting defined so far is ``default-server``, which is used to
select which of the following ``server:`` sections (see below) is used when to
server is explicitly specified on the command line.


``[server:*]`` section
======================

The configuration defines a **server** section for each server the Debusine
command can access. The section name starts with ``server:`` followed by the
server name.

Each server can be configured with these keys:

* ``api-url`` (mandatory): toplevel URL of the server API. In most cases it's
  the server ``/api`` URL.
* ``scope`` (mandatory): :ref:`scope <explanation-scopes>` to access with this server entry.
* ``token`` (mandatory): authentication token (see :ref:`create-api-token`).
