Integration testing for the astropy ecosystem
=============================================

This repository is a work in progress to develop a way to do integration testing
across the astropy ecosystem to ensure that the core and coordinated packages
work well together.

For now, you can test the latest astropy release with the latest coordinated
package releases (along with a few third-party packages that depend heavily
on astropy). Pre-releases are used if available. To run the tests, run:

    $ tox

or you can even run all environments in parallel with e.g.:

    $ tox --parallel 16
