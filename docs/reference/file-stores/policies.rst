.. _file-store-policies:

File store policies
===================

File stores have some policies that apply to the store as a whole, which can
be set using ``debusine-admin file_store create``:

* ``instance_wide`` (boolean, defaults to True): If True, this store can be
  used by any scope on this Debusine instance.  If False, it may only be
  used by a single scope.

* ``soft_max_size`` (integer, optional): A soft limit in bytes for the total
  capacity of the store.  This may be exceeded temporarily during uploads;
  ``debusine-admin vacuum_storage`` will move files from least-recently-used
  artifacts to another file store to get back below the limit.

* ``max_size`` (integer, optional): A hard limit in bytes for the total
  capacity of the store.  This may not be exceeded even temporarily during
  uploads.

There are also policies that apply to an individual scope's use of a store,
which can be set using ``debusine-manage scope add_file_store`` and
``debusine-manage scope edit_file_store``:

* ``upload_priority`` (integer, optional): The priority of this store for
  the purpose of storing new files.  When adding a new file, Debusine tries
  stores whose policies allow adding new files in descending order of upload
  priority, counting null as the lowest.

* ``download_priority`` (integer, optional): The priority of this store for
  the purpose of serving files to clients.  When downloading a file,
  Debusine tries stores in descending order of download priority, counting
  null as the lowest; it breaks ties in descending order of upload priority,
  again counting null as the lowest.  If there is still a tie, it picks one
  of the possibilities arbitrarily.

* ``populate`` (boolean, defaults to False): If True, ``debusine-admin
  vacuum_storage`` ensures that this store has a copy of all files in the
  scope.

* ``drain`` (boolean, defaults to False): If True, ``debusine-admin
  vacuum_storage`` moves all files in this scope to some other store in the
  same scope, following the same rules for finding a target store as for
  uploads of new files.  It does not move into a store if that would take
  its total size over ``soft_max_size`` (either for the scope or the file
  store), and it logs an error if it cannot find any eligible target store.

* ``drain_to`` (string, optional): If this field is set, then constrain
  ``drain`` to use the store with the given name in this scope.

* ``read_only`` (boolean, defaults to False): If True, Debusine will not add
  new files to this store.  Use this in combination with ``drain`` to
  prepare for removing the file store.

* ``write_only`` (boolean, defaults to False): If True, Debusine will not
  read files from this store.  This is suitable for provider storage classes
  that are designed for long-term archival rather than routine retrieval,
  such as S3 Glacier Deep Archive.  Debusine will only upload files to this
  store when applying the ``populate`` policy.

* ``soft_max_size`` (integer, optional): An integer specifying the number of
  bytes that the file store can hold for this scope (accounting files that
  are in multiple scopes to all of the scopes in question).  This limit may
  be exceeded temporarily during uploads; ``debusine-admin vacuum_storage``
  will move files from least-recently-created artifacts to another file
  store to get back below the limit.

Storage policy recommendations
------------------------------

The shared local storage should normally have the highest
``upload_priority`` of any store, in order not to block uploads of new files
on slow data transfers.  Its store-level ``soft_max_size`` field should be
set somewhat below the available file system size, with clearance for at
least a week's worth of uploads if possible.  That will give the daily
``debusine-admin vacuum_storage`` job time to move files from
least-recently-created artifacts to other file stores.

To guard against data loss, files may be in multiple stores: for example, a
backup store might use the ``populate`` policy to ensure that it has a copy
of all files, and perhaps ``write_only`` to ensure that Debusine does not
try to serve files directly from it.  Alternatively, an administrator might
use lower-level tools such as `rclone <https://rclone.org/>`__ to handle
backups.
