#!/bin/sh
set -eu

# The official Cassandra image intentionally exposes only a small subset of
# cassandra.yaml through environment variables.  The demo estate needs native
# password/RBAC behavior and authenticated remote JMX, so apply those exact
# Cassandra 5.0 settings before delegating to the image's own entrypoint.
sed -ri \
    -e 's/^authenticator:.*/authenticator: PasswordAuthenticator/' \
    -e 's/^authorizer:.*/authorizer: CassandraAuthorizer/' \
    -e "s/^native_transport_port:.*/native_transport_port: ${CDEADMIN_NATIVE_TRANSPORT_PORT:-9042}/" \
    -e "s/^storage_port:.*/storage_port: ${CDEADMIN_STORAGE_PORT:-7000}/" \
    -e 's/^materialized_views_enabled:.*/materialized_views_enabled: true/' \
    -e 's/^user_defined_functions_enabled:.*/user_defined_functions_enabled: true/' \
    "${CASSANDRA_CONF}/cassandra.yaml"

sed -ri \
    's/^JMX_PORT="7199"/JMX_PORT="${CDEADMIN_JMX_PORT:-7199}"/' \
    "${CASSANDRA_CONF}/cassandra-env.sh"

printf '%s %s\n' \
    "${CDEADMIN_JMX_USERNAME}" "${CDEADMIN_JMX_PASSWORD}" \
    > "${CASSANDRA_CONF}/jmxremote.password"
printf '%s readwrite\n' "${CDEADMIN_JMX_USERNAME}" \
    > "${CASSANDRA_CONF}/jmxremote.access"
chmod 600 \
    "${CASSANDRA_CONF}/jmxremote.password" \
    "${CASSANDRA_CONF}/jmxremote.access"

exec /usr/local/bin/docker-entrypoint.sh "$@"
