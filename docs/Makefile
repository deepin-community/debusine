# Minimal makefile for Sphinx documentation
#

# You can set these variables from the command line.
SPHINXOPTS    =
SPHINXBUILD   = sphinx-build
SOURCEDIR     = .
BUILDDIR      = _build
LINKCHECKOUTPUTDIR	= $(BUILDDIR)/linkcheck

# Put it first so that "make" without argument is like "make help".
help:
	@$(SPHINXBUILD) -M help "$(SOURCEDIR)" "$(BUILDDIR)" $(SPHINXOPTS) $(O)

.PHONY: help Makefile

linkcheck:
	# sphinx version 4.1 (Bookworm) has the option "linkcheck_allowed_redirects"
	# (https://www.sphinx-doc.org/en/master/usage/configuration.html#confval-linkcheck_allowed_redirects)
	@$(SPHINXBUILD) -b linkcheck "$(SOURCEDIR)" "$(LINKCHECKOUTPUTDIR)"
	echo "sphinx linkcheck report available in $(LINKCHECKOUTPUTDIR)"

livehtml:
	sphinx-autobuild "$(SOURCEDIR)" "$(BUILDDIR)" $(SPHINXOPTS) $(O) --open-browser

# Catch-all target: route all unknown targets to Sphinx using the new
# "make mode" option.  $(O) is meant as a shortcut for $(SPHINXOPTS).
%: Makefile
	@$(SPHINXBUILD) -M $@ "$(SOURCEDIR)" "$(BUILDDIR)" $(SPHINXOPTS) $(O)
