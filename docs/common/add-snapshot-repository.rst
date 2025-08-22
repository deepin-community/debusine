.. code-block:: console

  $ sudo tee /etc/apt/sources.list.d/debusine.sources <<END
  Types: deb
  URIs: https://freexian-team.pages.debian.net/debusine/repository/
  Suites: bookworm
  Components: main
  Architectures: all
  Signed-By: |
    -----BEGIN PGP PUBLIC KEY BLOCK-----

    mQENBGUAlgcBCAD0AhJt2KVLlrJ+Bx8VcjxCNGcOEMPOY1Db+F+8TiaYrSqZ07kx
    VOJYATlNAqCkQqCu89E9WvqKTg2kyNZchyArwi4bUhEfA+BT+KBeb4dGcoAdwUNX
    c4hx94M/Ow2hAStIni2TUYau99e5sY2QIFb03Lb3dShlQA2EesjQcvnpZCDBcVHW
    kYGj8zjh+i0QdtGVYJlXI/IskSJKexJuiOQ9uIGtsJ+VAm2hD1dTDWvGoUp2quWP
    uHGBouyzFkFQpCOXKBnfpF4h7LMFJSx9KNV/rSMZgLf0F9ukeGnjsST3okQGz2Sz
    E5WdoJuaLOgj/eCeTG2lHD1sdzyndQtG9sKJABEBAAG0Q0RlYnVzaW5lIFNhbHNh
    IFJlcG9zaXRvcnkgPHJhcGhhZWwrZGVidXNpbmUrc2Fsc2FyZXBvQGZyZWV4aWFu
    LmNvbT6JAU4EEwEKADgWIQSQH9XztPO3VNW3w993YWGYlEiOAAUCZQCWBwIbLwUL
    CQgHAwUVCgkICwUWAgMBAAIeAQIXgAAKCRB3YWGYlEiOAO8eCACwcqdDbVigGiv8
    x9iUOp59/6XMG/mtdSjzkkK5LVUEq9bkQAdLRbzb43p0vLl7E/7Yt3on62D6N3QB
    1ap710QsDRIN/CoKRi6sjHCiAqpSUC9LcwgLmnrLwUHDPDrkpsnWmQ7YINtV1P6E
    BFgdqqtw/1avMyliBbh3/XNRQkYq6xkcEZGIftqwX8GYB3M+cheefYe24H/JwLJm
    gerMz8MVEGKMzDm0OaLjWixV6Ga4Wkzo5ya1zg5OmROULAj3CsKDN76OCEwGGMBs
    kNsTB9aAisP8D6wW6ZXX5eChRLRCmKUEB5zpHJ++jdJ0yY38kF+N17w5eos+rc4s
    bMld2SQP
    =XfKL
    -----END PGP PUBLIC KEY BLOCK-----
  END
  $ sudo apt update
