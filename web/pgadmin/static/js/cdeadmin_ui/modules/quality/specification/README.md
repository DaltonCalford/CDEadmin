# Data Quality embedded specification

This directory contains the production-owned machine manifest and screen
contracts used to verify the first-party `cdeadmin.quality` implementation.
Their authority is the read-only CDEadmin First-Party Module Specifications
package, version 1.0.

The runtime never reads the external specification package. Live validation,
profiling, sampling, and baseline support remains unknown until a provider
registers and evidences the complete five-operation Data Quality adapter
contract. Provider-native details are retained without making other engines
pretend to support them.

Profiler proposals retain their sample reference and source revision and are
never added to an authored rule set without explicit acceptance. Failed-row
samples are bounded and use permissions separate from metadata results.
Great Expectations interoperability is loss-aware: known expectations map to
typed CDEadmin rules, unknown source content is preserved, and rules without a
safe inverse mapping are reported rather than guessed.
