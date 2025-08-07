# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Signon with external providers."""

# Code in this module is almost a standalone library, and may be split into one
# if there is a need of adopting it in multiple projects.
#
# Possible improvements that can be made to make this code more generic:
#
# * https://salsa.debian.org/freexian-team/debusine/-/issues/221
#   "Add a view to trigger linking other external auth providers to the current
#   user"
# * https://salsa.debian.org/freexian-team/debusine/-/issues/231
#   "Improvements to SSO-based account autocreation"
