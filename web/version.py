##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

##########################################################################
# Application settings
##########################################################################

# CDEadmin has its own product version. APP_VERSION remains the upstream
# compatibility/schema baseline because pgAdmin migrations and extension
# compatibility still consume its numeric form.
CDEADMIN_VERSION = '0.1.0-dev'
CDEADMIN_FORK_STATUS = 'Hard fork'
UPSTREAM_PRODUCT_NAME = 'pgAdmin 4'
UPSTREAM_BASE_VERSION = '9.17'

# NOTE!!!
# If you change any of APP_RELEASE, APP_REVISION or APP_SUFFIX, then you
# must also change APP_VERSION_INT to match.
#

# Application version number components
APP_RELEASE = 9
APP_REVISION = 17

# Application version suffix, e.g. 'beta1', 'dev'. Usually an empty string
# for GA releases.
APP_SUFFIX = ''

# Numeric application version for upgrade checks. Should be in the format:
# [X]XYYZZ, where X is the release version, Y is the revision, with a leading
# zero if needed, and Z represents the suffix, with a leading zero if needed
APP_VERSION_INT = 91700

# DO NOT CHANGE!
# The application version string, constructed from the components
if not APP_SUFFIX:
    APP_VERSION = '%s.%s' % (APP_RELEASE, APP_REVISION)
else:
    APP_VERSION = '%s.%s-%s' % (APP_RELEASE, APP_REVISION, APP_SUFFIX)
