/*
 * CDEadmin exact-profile qualification support for Apache Cassandra 5.0.8.
 *
 * Cassandra's sstableloader has no password-file option. This AuthProvider
 * keeps leased credentials out of the process argument vector by reading the
 * two values from the constrained environment created by ProviderToolRunner.
 */
package org.cdeadmin.cassandra;

import com.datastax.driver.core.PlainTextAuthProvider;

public final class CDEadminEnvironmentAuthProvider
    extends PlainTextAuthProvider
{
    private static final String USER_ENV =
        "CDEADMIN_CASSANDRA_TOOL_USERNAME";
    private static final String PASSWORD_ENV =
        "CDEADMIN_CASSANDRA_TOOL_PASSWORD";

    public CDEadminEnvironmentAuthProvider()
    {
        super(requiredEnvironment(USER_ENV), requiredEnvironment(PASSWORD_ENV));
    }

    private static String requiredEnvironment(String name)
    {
        String value = System.getenv(name);
        if (value == null || value.isEmpty())
            throw new IllegalStateException(
                "CDEadmin Cassandra tool credential environment is absent");
        return value;
    }
}
