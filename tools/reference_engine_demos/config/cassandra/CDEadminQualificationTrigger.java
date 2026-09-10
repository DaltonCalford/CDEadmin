/*
 * CDEadmin - Multi-engine Database Administration
 *
 * Copyright (C) 2013 - 2026, The pgAdmin Development Team
 * This software is released under the PostgreSQL Licence
 */

package org.cdeadmin.cassandra;

import java.util.Collection;
import java.util.Collections;

import org.apache.cassandra.db.Mutation;
import org.apache.cassandra.db.partitions.Partition;
import org.apache.cassandra.triggers.ITrigger;

/** A deterministic no-op trigger used only by the exact reference fixture. */
public final class CDEadminQualificationTrigger implements ITrigger
{
    @Override
    public Collection<Mutation> augment(Partition update)
    {
        return Collections.emptyList();
    }
}
