# Schema Comparison embedded specification

This directory contains the production-owned machine manifest used to verify
the first-party `cdeadmin.schema_compare` implementation. Its authority is the
read-only CDEadmin First-Party Module Specifications package, version 1.0.

The runtime does not read files outside the repository. Provider support is
never inferred: a live provider must register the complete Schema Comparison
adapter contract before its resources can be captured, compared, planned,
validated, or applied.
