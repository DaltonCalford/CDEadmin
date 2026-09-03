# CDEadmin Debian/Ubuntu Builds

CDEadmin is an independent hard fork of pgAdmin 4 9.17. See the repository
`NOTICE` and `LICENSE` files for upstream attribution and licence terms.

This directory contains the build runner script for creating .DEB packages for
Debian and Ubuntu. 

## Supported platforms

* Debian 11, 12 & 13
* Ubuntu 22.04, 24.04 & 25.04

## Build configuration

To build Debian/Ubuntu packages, first run the setup.sh script as root to
install the required pre-requisites, e.g.

    # pkg/debian/setup.sh

## Building packages

To build a set of packages, from the top-level source directory run:

    $ make debian

or:

    $ pkg/debian/build.sh

Four .deb packages will be created in the dist/ directory:

*cdeadmin_<version>_<distro>_<distro_version>_all.deb*

A convenience package that depends on all the others.

*cdeadmin-server_<version>_<distro>_<distro_version>_<arch>.deb*

The core server, e.g. the Python and JS code and the online documentation.

*cdeadmin-desktop_<version>_<distro>_<distro_version>_<arch>.deb*

The desktop runtime. Requires the server package.

*cdeadmin-web_<version>_<distro>_<distro_version>_<arch>.deb*

The server mode setup script for configuring Apache HTTPD. Requires the server 
package.

## Building a repo

An APT repo can be created by building DEBs for the required platforms, moving
them into the required directory structure, and then running a number of
commands to create the required metadata. The CDEadmin repos use the following
structure (which doesn't entirely follow the normal structure for APT, but
does seem to work well unlike other attempts):

    <root>
      bionic
        dists
          cdeadmin
            InRelease
            main
              binary-all
                Packages
                Packages.gz
                cdeadmin_4.21_all.deb
                cdeadmin-web_4.21_all.deb
              binary-amd64
                Packages
                Packages.gz
                cdeadmin-desktop_4.21_amd64.deb
                cdeadmin-server_4.21_amd64.deb
              binary-i386
                Packages
                Packages.gz
            Release
            Release.gpg
            Release.gz
      buster
      disco
      eoan
      focal
      README
      stretch
      xenial

Note that only the first branches are shown above; other branches (e.g. for
buster, disco etc. follow the structure shown for bionic.

Technically there are actually multiple repos here, one for each OS release.
Note also that the *binary-i386* directories do not contain any packages as we're
not building 32bit packages for Linux. The directories and package indexes are
present though, to prevent warnings being emitted on amd64 machines which are
configured to support 32bit packages as well.

In order to sign the repositories you need to import your signing private key
into the gnupg2 keystore, for example:

    gpg --import signing_key.priv

Once the files are in the right structure, we need to run a number of commands
to generate the metadata, and sign the relevant files using GPG (in APT, we
sign the repository indexes rather than the packages themselves.

To create the metadata, first we create a config file for the *apt-ftparchive*
program in *$HOME/aptftp.conf* (without the start/end markers):

    APT::FTPArchive::Release {
      Origin "CDEadmin contributors";
      Label "CDEadmin";
      Suite "cdeadmin";
      Architectures "amd64 all";
      Components "main";
      Description "CDEadmin - Development Tools for PostgreSQL";
    };
    Default {
        Packages::Compress ". gzip bzip2";
        Sources::Compress ". gzip bzip2";
        Contents::Compress ". gzip bzip2";
    };

Next, we create the package indexes. Run the following command for each OS
release to be included (in the example, we're using bionic):

    for ARCH in all amd64 i386; do cd <root>/bionic && apt-ftparchive packages -c=$HOME/aptftp.conf dists/cdeadmin/main/binary-${ARCH}  > dists/cdeadmin/main/binary-${ARCH}/Packages && gzip -k dists/cdeadmin/main/binary-${ARCH}/Packages; done

Now we need to create the release file for each OS release (again, using bionic
in the example:

    cd <root>/bionic/dists/cdeadmin && apt-ftparchive release -c=$HOME/aptftp.conf . > Release && gzip -k Release

Finally, we can sign the release files. Replace <key name> with the email
address on your signing key:

    cd <root>/bionic/dists/cdeadmin && gpg -u <key name> -bao Release.gpg Release
    cd <root>/bionic/dists/cdeadmin && gpg -u <key name> --clear-sign --output InRelease Release

Note that it is important to run each command in the correct directory (hence
the cd commands) to ensure the relative paths are created correctly in the
indexes.

## Repository Configuration

CDEadmin repo configurations live in */etc/apt/sources.list.d/cdeadmin.list*. The
file can be created with a command such as:

    sudo sh -c 'echo "deb https://server.company.com/apt/$(lsb_release -cs) cdeadmin main" > /etc/apt/sources.list.d/cdeadmin.list && apt update'

Assuming that <root> in the repository structure corresponds to
https://server.company.com/apt/ from the client's perspective.

If you sign the repository, distribute the independently controlled CDEadmin
public key through the CDEadmin repository endpoint. No signing key or endpoint
is assigned yet, and the upstream pgAdmin key must never be reused.
