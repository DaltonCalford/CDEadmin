#!/bin/bash

# Exit when any command fails
set -e

# Debugging shizz
trap 'ERRCODE=$? && if [ ${ERRCODE} -ne 0 ]; then echo "The command \"${BASH_COMMAND}\" failed in \"${FUNCNAME}\" with exit code ${ERRCODE}."; fi' EXIT

OS_ARCH=$(dpkg-architecture -qDEB_HOST_ARCH)
DISTRO=$(lsb_release -cs)
RELEASE=1

# Stop creating pyc files.
export PYTHONDONTWRITEBYTECODE=1

# Common Linux build functions
source pkg/linux/build-functions.sh

# Assemble the "standard" installation footprint
_setup_env "$0" "debian"
_cleanup "deb"
_setup_dirs
_create_python_virtualenv "debian"
_build_runtime
_build_docs "debian"
_copy_code "debian"
_generate_sbom

#
# Server package
#

# Create the Debian packaging stuffs for the server
echo "Creating the server package..."
mkdir "${SERVERROOT}/DEBIAN"

echo "Creating preinst script..."
cat << EOF > "${SERVERROOT}/DEBIAN/preinst"
#!/bin/sh

rm -rf /usr/cdeadmin/venv
if [ -d /usr/cdeadmin/web ]; then
  cd /usr/cdeadmin/web && rm -rf \$(ls -A -I config_local.py)
fi
EOF

chmod 755 "${SERVERROOT}/DEBIAN/preinst"

cat << EOF > "${SERVERROOT}/DEBIAN/control"
Package: ${APP_NAME}-server
Version: ${APP_LONG_VERSION}
Architecture: ${OS_ARCH}
Section: database
Priority: optional
Depends: ${PYTHON_BINARY}, libpq5 (>= 11.0), libgssapi-krb5-2, python-dbus | python3-dbus
Recommends: postgresql-client | postgresql-client-15 | postgresql-client-14 | postgresql-client-13 | postgresql-client-12 | postgresql-client-11 | postgresql-client-10
Maintainer: CDEadmin contributors <cdeadmin@localhost>
Description: The CDEadmin server for provider-driven multi-engine and multi-model database administration.
EOF

# Build the Debian package for the server
chmod -R u+rwX,go+rX,go-w "${SERVERROOT}"
fakeroot dpkg-deb --build "${SERVERROOT}" "${DISTROOT}/${APP_NAME}-server_${APP_LONG_VERSION}-${RELEASE}.${DISTRO}_${OS_ARCH}.deb"

#
# Desktop package
#

# Create the Debian packaging stuffs for the desktop
echo "Creating the desktop package..."
mkdir "${DESKTOPROOT}/DEBIAN"

# Ubuntu 24 requires apparmor profile to work.
OS_ID=$(grep "^ID=" /etc/os-release | awk -F "=" '{ print $2 }')
OS_VERSION=$(grep "^VERSION_ID=" /etc/os-release | awk -F "=" '{ print $2 }' | sed 's/"//g' | awk -F "." '{ print $1 }')

if [ "${OS_ID}" == 'ubuntu' ] && [ "${OS_VERSION}" -ge "24" ]; then
  cat << EOF > "${DESKTOPROOT}/DEBIAN/conffiles"
/etc/apparmor.d/cdeadmin
EOF

  mkdir -p "${DESKTOPROOT}/etc/apparmor.d"
  cp "${SOURCEDIR}/pkg/debian/cdeadmin-aa-profile" "${DESKTOPROOT}/etc/apparmor.d/cdeadmin"

  cat << EOF > "${DESKTOPROOT}/DEBIAN/postinst"
#!/bin/sh

echo "Load AppArmor CDEadmin profile..."
if command -v apparmor_parser >/dev/null 2>&1; then
  apparmor_parser -r /etc/apparmor.d/cdeadmin
else
  echo "Warning: apparmor_parser not found; CDEadmin desktop may not work on Ubuntu 24+ with userns restrictions."
fi
EOF
  chmod 755 "${DESKTOPROOT}/DEBIAN/postinst"
fi

cat << EOF > "${DESKTOPROOT}/DEBIAN/control"
Package: ${APP_NAME}-desktop
Version: ${APP_LONG_VERSION}
Architecture: ${OS_ARCH}
Section: database
Priority: optional
Depends: ${APP_NAME}-server (= ${APP_LONG_VERSION}), libatomic1, xdg-utils, python-dbus | python3-dbus
Maintainer: CDEadmin contributors <cdeadmin@localhost>
Description: The CDEadmin desktop interface for multi-engine and multi-model database administration.
EOF

# Build the Debian package for the desktop
chmod -R u+rwX,go+rX,go-w "${DESKTOPROOT}"
fakeroot dpkg-deb --build "${DESKTOPROOT}" "${DISTROOT}/${APP_NAME}-desktop_${APP_LONG_VERSION}-${RELEASE}.${DISTRO}_${OS_ARCH}.deb"

#
# Web package
#

# Create the Debian packaging stuffs for the web
echo "Creating the web package..."
mkdir "${WEBROOT}/DEBIAN"

cat << EOF > "${WEBROOT}/DEBIAN/conffiles"
/etc/apache2/conf-available/cdeadmin.conf
EOF

cat << EOF > "${WEBROOT}/DEBIAN/control"
Package: ${APP_NAME}-web
Version: ${APP_LONG_VERSION}
Architecture: all
Section: database
Priority: optional
Depends: ${APP_NAME}-server (= ${APP_LONG_VERSION}), apache2, libapache2-mod-wsgi-py3
Maintainer: CDEadmin contributors <cdeadmin@localhost>
Description: The CDEadmin multi-engine web interface, hosted under Apache HTTPD.
EOF

mkdir -p "${WEBROOT}/etc/apache2/conf-available"
cp "${SOURCEDIR}/pkg/debian/cdeadmin.conf" "${WEBROOT}/etc/apache2/conf-available"

# Build the Debian package for the web
chmod -R u+rwX,go+rX,go-w "${WEBROOT}"
fakeroot dpkg-deb --build "${WEBROOT}" "${DISTROOT}/${APP_NAME}-web_${APP_LONG_VERSION}-${RELEASE}.${DISTRO}_all.deb"

#
# Meta package
#

# Create the Debian packaging stuffs for the meta package
echo "Creating the meta package..."
mkdir "${METAROOT}/DEBIAN"

cat << EOF > "${METAROOT}/DEBIAN/control"
Package: ${APP_NAME}
Version: ${APP_LONG_VERSION}
Architecture: all
Section: database
Priority: optional
Depends: ${APP_NAME}-server (= ${APP_LONG_VERSION}), ${APP_NAME}-desktop (= ${APP_LONG_VERSION}), ${APP_NAME}-web (= ${APP_LONG_VERSION})
Maintainer: CDEadmin contributors <cdeadmin@localhost>
Description: Installs all required components to run CDEadmin in desktop and web modes.
EOF

# Build the Debian meta package
fakeroot dpkg-deb --build "${METAROOT}" "${DISTROOT}/${APP_NAME}_${APP_LONG_VERSION}-${RELEASE}.${DISTRO}_all.deb"

# Get the libpq package
pushd "${DISTROOT}" 1> /dev/null
apt-get download libpq5 libpq-dev
popd 1> /dev/null

echo "Completed. DEBs created in ${DISTROOT}."
