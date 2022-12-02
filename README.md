Integration testing for the Astropy ecosystem
=============================================

This repository is a way to do integration testing
across the Astropy ecosystem to ensure that the core and coordinated packages
work well together.

The tests here only do basic testings for those packages on Linux.
Individual packages should still do the due diligence to test against
dev and/or pre-release versions of `astropy` on their own to be sure.

To run these tests on GitHub Action as a maintainer of this repo:

1. Goto Actions tab.
2. Select `astropy_rc_basic` job.
3. Click "Run workflow" dropdown and then the green "Run workflow" button.
4. A new run should kick off after a few seconds. Monitor the logs of this run.
