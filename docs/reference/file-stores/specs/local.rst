.. _file-backend-local:

Local file backend
==================

* Configuration:

  * ``base_directory`` (string, optional): The base directory for the store
    on the server; all its files are stored under here.  For the "Default"
    store, the base directory is optional and defaults to the
    ``DEBUSINE_STORE_DIRECTORY`` Django setting; for all other stores, it is
    required.

* Supports returning local paths: yes
* Supports returning URLs: no
