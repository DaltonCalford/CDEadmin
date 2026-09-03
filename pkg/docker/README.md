# CDEadmin Docker Container Builds

CDEadmin is an independent hard fork of pgAdmin 4 9.17. See the repository
`NOTICE` and `LICENSE` files for upstream attribution and licence terms.

This directory contains the files required to create a docker container running
CDEadmin.

## Building

From the top level directory of the CDEadmin source tree, simply run:

    docker build .

You can also run *make docker*, which will call *docker build .* but also tag
the image like:

    cdeadmin:latest cdeadmin:0.1 cdeadmin:0.1.0-dev

### WARNING 

The build should be run in a CLEAN source tree. Whilst some potentially
dangerous files such as config_local.py or log files will be explicitly
excluded from the final image, other files will not be.

## Running

See the documentation at *docs/en_US/container_deployment.rst* for information on
running the container.
