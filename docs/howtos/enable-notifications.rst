.. _configure-notifications:

======================================
Enable notifications for work requests
======================================

Currently Debusine only supports email notifications and can send
notifications when a work request completes. Getting this to work involves
setting up a notification channel and configuring the work request to
trigger notifications.

Pre-requisites
--------------

The Debusine server needs to be able to send emails. By default it uses
the local mail transport agent, so either it needs to be configured and
working, or you can :ref:`configure Debusine to use an external SMTP server
<smtp-configuration>`.

Setting up an email-based notification channel
----------------------------------------------

Notifications channels are created by the Debusine administrator with
the ``debusine-admin create_notification_channel`` command.

To create a notification channel with the name ``admin-team`` and type
``email`` (the only supported channel type at the moment) to send emails
to ``admin@example.org`` you would do:

.. code-block:: console

  $ sudo -u debusine-server debusine-admin create_notification_channel admin-team email << EOF
  {
    "from": "nobody@example.org",
    "to": ["admin@example.org"]
  }
  EOF

``from`` and ``to`` are required fields. ``cc`` (list of emails) and ``subject``
are optional fields. The default subject is ``WorkRequest {work_request_id}
completed in {work_request_result}``, and the ``{work_request_id}``
and ``{work_request_result}`` are replaced by the id and result (success,
failure or error)

Related commands of ``create_notification_channel`` are
``delete_notification_channel``, ``list_notification_channels`` or
``manage_notification_channel``. See
:ref:`debusine-admin-notification-channels`.

Configuring a work request with a notification
----------------------------------------------

Once you have a notification channel setup, you can add the
``--event-reactions`` option to any work request that you would submit. The key
value should be a dictionary mapping an event to a list of 
channels that must be notified. Right now the supported events
are ``on_failure`` and ``on_error``.

For instance, with the command below, and with the notification channel
configured in the former section, any failure to build the package
would result in an email notification to ``admin@example.org`` with
a copy to ``qa-team@example.org``:

.. code-block:: console

    $ cat qa.yaml
    on_failure:
    - action: send-notification
      channel: admin-team
      data:
        cc: [qa-team@example.org]
        subject: 'Work request ${work_request_id}: result: ${work_request_result}'
    
    $ debusine create-work-request ... --event-reactions qa.yaml

In this case, the main recipients (``To`` field) is defined by the
notification channel configuration, but the work request overrides
the ``Cc`` field (recipients in carbon copy) and the subject (with
a templated value that works like the value that can be configured
in the notification channel directly).

The possible ``data`` fields for email notifications are:

- ``from``: a default is provided by the Debusine admin
- ``to``: a default is provided by the Debusine admin
- ``subject``: a default is provided by the Debusine admin
- ``cc``: list of emails
