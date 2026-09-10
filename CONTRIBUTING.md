# Contributing to ScratchRobin CDE Admin

Thank you for your interest in contributing to CDEadmin.

The project is currently in heavy development and is not accepting external
contributions. Defects and security reports must be sent to CDEadmin's own
project channels, not to the pgAdmin Development Team.

Current work must follow these rules:

- Provider code describes only behavior supported by the exact engine.
- Shared UI components may provide layout but may not invent shared engine
  semantics or silently substitute PostgreSQL behavior.
- A supported mutation needs a provider-owned form, validation, preview,
  execution path, live evidence and browser review.
- Secrets must not appear in routes, logs, screenshots or retained evidence.
- Native transaction completion belongs to the provider session; common code
  must not replay mutations or infer commit success.
- Existing upstream copyright headers and the PostgreSQL Licence must remain.
- New project-owned names use ScratchRobin/CDEadmin naming unless an inherited
  compatibility interface requires otherwise.

Development reports, workplans and QA evidence belong under the external
``~/Sandbox/pgadmin4_work_area/`` workspace. Product code and documentation
belong in this repository. ScratchBird source/specifications and donor-engine
source trees are read-only research inputs.
