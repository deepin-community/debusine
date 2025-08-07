# Debusine

Debusine is a modern cloud-powered Continuous Integration (CI)
platform to run many packaging and distribution related workflows
for the Debian ecosystem. It can be used to automate everything from the
package build up to the generation of installer/disk/cloud/container
images, including all the intermediary quality assurance checks. It is
very versatile and easy to extend to cater to custom requirements.

To cope with the scale (dozens of thousands of packages), and with the
breadth of supported CPU architectures of a Linux distribution, Debusine
manages the scheduling and distribution of individual tasks to distributed
worker machines, including cloud workers on demand.

If you are new to Debusine, you will want to read
[Introduction to
Debusine](https://freexian-team.pages.debian.net/debusine/explanation/introduction.html)
first.

## Why this project?

Have a look at [the dedicated page in the
documentation](https://freexian-team.pages.debian.net/debusine/explanation/why.html).

## Documentation

The [documentation](https://freexian-team.pages.debian.net/debusine/) always
matches what's in the git repository's master branch.

Otherwise you can generate the documentation yourself by doing `make html`
in the docs subdirectory of the debusine git repository.

## Interacting with the project

### How to contribute

Have a look at the ["Contribute to Debusine"
section](https://freexian-team.pages.debian.net/debusine/howtos/contribute.html) of the
documentation.

### Contact information

You can interact with the developers through the [issue
tracker](https://salsa.debian.org/freexian-team/debusine/-/issues)
or on the `#debusine` IRC channel on the OFTC network (irc.debian.org
server for example).

Raphaël Hertzog (buxy on IRC) initiated the project and oversees its
development. Debusine is a free software project, and its development
has been supported by [various
entities](https://freexian-team.pages.debian.net/debusine/reference/sponsors.html).

### Reporting bugs and vulnerabilities

We are using [GitLab's bug
tracker](https://salsa.debian.org/freexian-team/debusine/-/issues) to
manage bug reports which are related to the source code itself. You should
file new bugs there.

Security issues should be reported to the bug tracker like other bugs
but they can be marked as confidential if the issue is really sensitive.
If you are unsure, start with a confidential issue, we can always
make it public later.
