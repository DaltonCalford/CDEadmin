#!/bin/bash

# Exit when any command fails
set -e

# Debugging shizz
trap 'ERRCODE=$? && if [ ${ERRCODE} -ne 0 ]; then echo "The command \"${BASH_COMMAND}\" failed in \"${FUNCNAME}\" with exit code ${ERRCODE}."; fi' EXIT

OS_ARCH=$(arch)

# Make sure we get the latest libpq
export PATH=$(ls -d /usr/pgsql-1* | sort -r | head -1)/bin:$PATH

# Stop creating pyc files.
export PYTHONDONTWRITEBYTECODE=1

# Common Linux build functions
# shellcheck disable=SC1091
source pkg/linux/build-functions.sh

# Assemble the "standard" installation footprint
_setup_env "$0" "redhat"
_cleanup "rpm"
_setup_dirs
_create_python_virtualenv "redhat"
_build_runtime
_build_docs "redhat"
_copy_code "redhat"
_generate_sbom

# Get an RPM-compatible version number
RPM_VERSION=${APP_RELEASE}.${APP_REVISION}
if [ -n "${APP_SUFFIX}" ]; then
    RPM_VERSION=${RPM_VERSION}_${APP_SUFFIX}
fi

#
# Server package
#

# Create the Redhat packaging stuffs for the server
echo "Creating the server package..."

cat << EOF > "${BUILDROOT}/server.spec"
%global __requires_exclude_from ^/.*$
%global __provides_exclude_from ^/.*$
%global _build_id_links none

# Disable RPATH checking, as it will fail with some of the paths in the Python
# virtualenv, in particular Pillow.libs.
%global __brp_check_rpaths %{nil}

# Don't strip binaries when packaging them as this might break cpython modules
%define __strip /bin/true

%undefine __brp_mangle_shebangs
%undefine __brp_ldconfig

Name:		${APP_NAME}-server
Version:	${RPM_VERSION}
Release:	1%{?dist}
Summary:	The CDEadmin multi-engine administration server.
License:	PostgreSQL

Requires:	${PYTHON_BINARY}, libpq5, krb5-libs

%description
The CDEadmin server for provider-driven multi-engine and multi-model database administration. Derived from pgAdmin 4 under the PostgreSQL Licence.

%pre
rm -rf /usr/cdeadmin/venv
if [ -d /usr/cdeadmin/web ]; then
  cd /usr/cdeadmin/web && rm -rf \$(ls -A -I config_local.py)
fi

%build

%install
cp -rfa %{pga_build_root}/server/* \${RPM_BUILD_ROOT}

%files
%defattr(-,root,root,755)
%dir /usr/cdeadmin
/usr/cdeadmin/*
EOF

# Build the Redhat package for the server
rpmbuild --define "pga_build_root ${BUILDROOT}" -bb "${BUILDROOT}/server.spec"

#
# Desktop package
#

# Create the Redhat packaging stuffs for the desktop
echo "Creating the desktop package..."

cat << EOF > "${BUILDROOT}/desktop.spec"
%global __requires_exclude_from ^/.*$
%global __provides_exclude_from ^/.*$
%global _build_id_links none

%undefine __brp_mangle_shebangs
%undefine __brp_ldconfig

Name:		${APP_NAME}-desktop
Version:	${RPM_VERSION}
Release:	1%{?dist}
Summary:	The CDEadmin desktop user interface.
License:	PostgreSQL
Requires:	${APP_NAME}-server = ${RPM_VERSION}, libatomic, xdg-utils

%description
The CDEadmin desktop interface for multi-engine and multi-model database administration.

%build

%install
cp -rfa %{pga_build_root}/desktop/* \${RPM_BUILD_ROOT}

%post
/bin/xdg-icon-resource forceupdate

%files
%defattr(-,root,root,755)
%attr(755,root,root) /usr/cdeadmin/bin/cdeadmin
/usr/cdeadmin/bin/*
/usr/share/icons/hicolor/128x128/apps/*
/usr/share/icons/hicolor/64x64/apps/*
/usr/share/icons/hicolor/48x48/apps/*
/usr/share/icons/hicolor/32x32/apps/*
/usr/share/icons/hicolor/16x16/apps/*
/usr/share/applications/*
/usr/cdeadmin/sbom-desktop.json
EOF



# Build the Redhat package for the desktop
rpmbuild --define "pga_build_root ${BUILDROOT}" -bb "${BUILDROOT}/desktop.spec"


#
# Web package
#

# Create the Redhat packaging stuffs for the web
echo "Creating the web package..."

cat << EOF > "${BUILDROOT}/web.spec"
%global __requires_exclude_from ^/.*$
%global __provides_exclude_from ^/.*$
%global _build_id_links none

%undefine __brp_mangle_shebangs
%undefine __brp_ldconfig

Name:		${APP_NAME}-web
Version:	${RPM_VERSION}
Release:	1%{?dist}
BuildArch:	noarch
Summary:	The CDEadmin web interface, hosted under Apache HTTPD.
License:	PostgreSQL
Requires:	${APP_NAME}-server = ${RPM_VERSION}, httpd, ${PYTHON_BINARY_WITHOUT_DOTS}-mod_wsgi, policycoreutils-python-utils

%description
The CDEadmin provider-driven multi-engine web interface, hosted under Apache HTTPD.

%build

%install
cp -rfa %{pga_build_root}/web/* \${RPM_BUILD_ROOT}

%files
%defattr(-,root,root,755)
/usr/cdeadmin/bin/*
%config(noreplace) /etc/httpd/conf.d/*
/usr/cdeadmin/sbom-web.json
EOF

mkdir -p "${WEBROOT}/etc/httpd/conf.d"
cp "${SOURCEDIR}/pkg/redhat/cdeadmin.conf" "${WEBROOT}/etc/httpd/conf.d"

# Build the Redhat package for the web
rpmbuild --define "pga_build_root ${BUILDROOT}" -bb "${BUILDROOT}/web.spec"

#
# Meta package
#


# Create the Redhat packaging stuffs for the meta package
echo "Creating the meta package..."

cat << EOF > "${BUILDROOT}/meta.spec"
%global __requires_exclude_from ^/.*$
%global __provides_exclude_from ^/.*$
%global _build_id_links none

%undefine __brp_mangle_shebangs
%undefine __brp_ldconfig

Name:		${APP_NAME}
Version:	${RPM_VERSION}
Release:	1%{?dist}
BuildArch:	noarch
Summary:	Installs all CDEadmin desktop and web components.
License:	PostgreSQL
Requires:	${APP_NAME}-server = ${RPM_VERSION}, ${APP_NAME}-desktop = ${RPM_VERSION}, ${APP_NAME}-web = ${RPM_VERSION}

%description
Installs all required components to run CDEadmin in desktop and web modes.

%build

%install

%files

EOF

# Build the Redhat package for the meta package
rpmbuild --define "pga_build_root ${BUILDROOT}" -bb "${BUILDROOT}/meta.spec"

# Get the libpq we need
yumdownloader -y --downloadonly --destdir="${DISTROOT}" libpq5 libpq5-devel postgresql$(ls -d /usr/pgsql-1* | sort -r | head -1 | awk -F '-' '{ print $2 }')-libs

#
# Get the results!
#
cp "${HOME}/rpmbuild/RPMS/${OS_ARCH}/${APP_NAME}-"*"${RPM_VERSION}-"*".${OS_ARCH}.rpm" "${DISTROOT}/"
cp "${HOME}/rpmbuild/RPMS/noarch/${APP_NAME}-"*"${RPM_VERSION}-"*".noarch.rpm" "${DISTROOT}/"

echo "Completed. RPMs created in ${DISTROOT}."
