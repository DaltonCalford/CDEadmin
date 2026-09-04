/*
 * CDEadmin Apache Ignite qualification fixture.
 *
 * Copyright (C) 2026 CDEadmin contributors
 * Released under the PostgreSQL Licence.
 */

package org.cdeadmin.ignitefixture;

import java.util.Collection;
import java.util.Collections;
import java.util.List;
import java.util.concurrent.atomic.AtomicBoolean;
import org.apache.ignite.Ignite;
import org.apache.ignite.Ignition;
import org.apache.ignite.compute.ComputeJob;
import org.apache.ignite.compute.ComputeJobAdapter;
import org.apache.ignite.compute.ComputeJobResult;
import org.apache.ignite.compute.ComputeTaskName;
import org.apache.ignite.compute.ComputeTaskSplitAdapter;
import org.apache.ignite.configuration.IgniteConfiguration;
import org.apache.ignite.services.Service;
import org.apache.ignite.services.ServiceContext;


/** Supplies cancellable task and service instances for exact live gates. */
public final class CdeAdminIgniteFixture {
    private CdeAdminIgniteFixture() {
    }

    /** Deploy one cluster-singleton service, then detach the client. */
    public static void main(String[] arguments) {
        if (arguments.length != 2 || !"deploy-service".equals(arguments[0])) {
            throw new IllegalArgumentException(
                "usage: deploy-service <service-name>");
        }
        IgniteConfiguration configuration = new IgniteConfiguration();
        configuration.setClientMode(true);
        configuration.setIgniteInstanceName("cdeadmin-fixture-deployer");
        try (Ignite ignite = Ignition.start(configuration)) {
            ignite.services().deployClusterSingleton(
                arguments[1], new CancellableService());
        }
    }

    /** A service which terminates only after Ignite delivers cancellation. */
    public static final class CancellableService implements Service {
        private static final long serialVersionUID = 1L;
        private final AtomicBoolean cancelled = new AtomicBoolean(false);

        @Override
        public void init(ServiceContext context) {
        }

        @Override
        public void execute(ServiceContext context) throws Exception {
            while (!cancelled.get() && !context.isCancelled()) {
                Thread.sleep(100L);
            }
        }

        @Override
        public void cancel(ServiceContext context) {
            cancelled.set(true);
        }
    }

    /** A named, asynchronous REST task that remains visible until cancelled. */
    @ComputeTaskName("CdeAdminLongTask")
    public static final class LongTask
            extends ComputeTaskSplitAdapter<String, String> {
        private static final long serialVersionUID = 1L;

        @Override
        protected Collection<? extends ComputeJob> split(
                int gridSize, String argument) {
            return Collections.singletonList(new LongJob(argument));
        }

        @Override
        public String reduce(List<ComputeJobResult> results) {
            return results.iterator().next().getData();
        }
    }

    /** Cooperative job used by the task cancellation gate. */
    private static final class LongJob extends ComputeJobAdapter {
        private static final long serialVersionUID = 1L;
        private final AtomicBoolean cancelled = new AtomicBoolean(false);
        private final String value;

        LongJob(String value) {
            this.value = value;
        }

        @Override
        public Object execute() {
            while (!cancelled.get()) {
                try {
                    Thread.sleep(100L);
                } catch (InterruptedException exception) {
                    Thread.currentThread().interrupt();
                    cancelled.set(true);
                }
            }
            return value;
        }

        @Override
        public void cancel() {
            cancelled.set(true);
        }
    }
}
