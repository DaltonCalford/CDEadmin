#!/bin/bash

# Set the repo RPM version and build
REPO_RPM_VERSION=2
REPO_RPM_BUILD=1

# A CDEadmin repository endpoint must be assigned explicitly. Never publish a
# hard-fork package through the upstream pgAdmin repository.
if [ "${CDEADMIN_REPO_DIR}" == "" ]; then
    echo "CDEADMIN_REPO_DIR is required; no CDEadmin repository is assigned."
    exit 2
fi

# Exit when any command fails
set -e

# Debugging shizz
trap 'ERRCODE=$? && if [ ${ERRCODE} -ne 0 ]; then echo "The command \"${BASH_COMMAND}\" failed in \"${FUNCNAME}\" with exit code ${ERRCODE}."; fi' EXIT

# Common Linux build functions
# shellcheck disable=SC1091
source pkg/linux/build-functions.sh

# Are we installing a package key?
INCLUDE_KEY=0
if [ -f "pkg/redhat/CDEADMIN_PKG_KEY" ]; then
    INCLUDE_KEY=1
    echo "Building repo RPMs including a package signing key..."
else
    echo "pkg/redhat/CDEADMIN_PKG_KEY not found."
    echo "Building repo RPMs WITHOUT a package signing key..."
    sleep 5
fi

# Create the Redhat packaging stuffs for the repo
_create_repo_rpm() {
    DISTRO=$1
    if [ "${DISTRO}" == "redhat" ]; then
        PLATFORM="rhel"
    else
        PLATFORM=${DISTRO}
    fi

    echo "Creating the repo package for ${DISTRO}..."
    test -d "${BUILDROOT}/${DISTRO}-repo/etc/yum.repos.d" || mkdir -p "${BUILDROOT}/${DISTRO}-repo/etc/yum.repos.d"

    cat << EOF > "${BUILDROOT}/${DISTRO}-repo/etc/yum.repos.d/cdeadmin.repo"
[CDEadmin]
name=CDEadmin
baseurl=${CDEADMIN_REPO_DIR}/${DISTRO}/${PLATFORM}-\$releasever-\$basearch
enabled=1
EOF

    if [ ${INCLUDE_KEY} -eq 1 ]; then
        {
            echo repo_gpgcheck=1
            echo gpgcheck=1
            echo gpgkey=file:///etc/pki/rpm-gpg/CDEADMIN_PKG_KEY
        } >> "${BUILDROOT}/${DISTRO}-repo/etc/yum.repos.d/cdeadmin.repo"
    else
        echo gpgcheck=0 >> "${BUILDROOT}/${DISTRO}-repo/etc/yum.repos.d/cdeadmin.repo"
    fi

    echo "Creating the spec file for ${DISTRO}..."
    cat << EOF > "${BUILDROOT}/${DISTRO}-repo.spec"
%global __requires_exclude_from ^/.*$
%global __provides_exclude_from ^/.*$

Name:		${APP_NAME}-${DISTRO}-repo
Version:	${REPO_RPM_VERSION}
Release:	${REPO_RPM_BUILD}
BuildArch:	noarch
Summary:	Repository configuration for the CDEadmin ${DISTRO^} repositories.
License:	PostgreSQL
URL:		${CDEADMIN_REPO_DIR}

%description
The yum repository configuration for ${DISTRO^} platforms.

%build

%install
cp -rfa %{pga_build_root}/${DISTRO}-repo/* \${RPM_BUILD_ROOT}

%files
/etc/yum.repos.d/cdeadmin.repo
EOF

    if [ ${INCLUDE_KEY} -eq 1 ]; then
        cat << EOF >> "${BUILDROOT}/${DISTRO}-repo.spec"
/etc/pki/rpm-gpg/CDEADMIN_PKG_KEY
EOF
        test -d "${BUILDROOT}/${DISTRO}-repo/etc/pki/rpm-gpg" || mkdir -p "${BUILDROOT}/${DISTRO}-repo/etc/pki/rpm-gpg"
        cp pkg/redhat/CDEADMIN_PKG_KEY "${BUILDROOT}/${DISTRO}-repo/etc/pki/rpm-gpg/"
    fi

    # Build the package
    echo "Building the repo RPM for ${DISTRO}..."
    rpmbuild --define "pga_build_root ${BUILDROOT}" -bb "${BUILDROOT}/${DISTRO}-repo.spec"
}

_setup_env "$0" "redhat"
_create_repo_rpm redhat
_create_repo_rpm fedora

#
# Get the results!
#
test -d "${DISTROOT}/" || mkdir -p "${DISTROOT}/"
cp "${HOME}/rpmbuild/RPMS/noarch/${APP_NAME}-"*"-repo-${REPO_RPM_VERSION}-${REPO_RPM_BUILD}.noarch.rpm" "${DISTROOT}/"
