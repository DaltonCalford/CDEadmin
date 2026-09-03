# CDEadmin macOS Builds

CDEadmin is an independent hard fork of pgAdmin 4 9.17. See the repository
`NOTICE` and `LICENSE` files for upstream attribution and licence terms.
The `PGADMIN_*` build variables below are retained compatibility interfaces;
they do not identify the product.

## Required Packages

Either build the sources or get them from macports or similar:

1. Yarn & NodeJS

2. PostgreSQL 12 or above from http://www.postgresql.org/

3. Python 3.6+ (required for building). The build environment should run this 
  version of python in response to the *python* command.
  
## Building

1. To bundle a different version of Python from the default of 3.14.7, set the
   *PGADMIN_PYTHON_VERSION* environment variable, e.g:

       export PGADMIN_PYTHON_VERSION=3.13.11

2. If a path different from the default of /usr/local/pgsql for the PostgreSQL
   installation has been used, set the *PGADMIN_POSTGRES_DIR* environment variable
   appropriately, e.g:

       export PGADMIN_POSTGRES_DIR=/opt/local/pgsql

3. If you want to codesign the appbundle, copy *codesign.conf.in* to
   *codesign.conf* and set the values accordingly.

3. If you want to notarize the appbundle, copy *notarization.conf.in* to
   *notarization.conf* and set the values accordingly. Note that notarization
   will fail if the code isn't signed.
   
4. To build only DMG file, go to CDEadmin source root directory and execute:

       make appbundle

   To build both DMG and ZIP files, go to CDEadmin source root directory and execute:

       make appbundle BUILD_OPTS="--zip"
       
   This will create the python virtual environment and install all the required
   python modules mentioned in the requirements file using pip, build the
   runtime code and finally create the app bundle and the DMG and/or ZIP in *./dist*
   directory.
