.. code-block:: console

  $ curl -s https://freexian-team.pages.debian.net/debusine/repository/public-key.asc \
    | sudo tee /etc/apt/trusted.gpg.d/debusine.asc
  $ sudo tee /etc/apt/sources.list.d/debusine.list <<END
  deb [arch=all] https://freexian-team.pages.debian.net/debusine/repository/ bookworm main
  END
  $ sudo apt update
