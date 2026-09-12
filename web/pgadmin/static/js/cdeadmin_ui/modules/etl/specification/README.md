# Embedded ETL Designer machine contracts

These JSON files are exact, read-only-derived copies of the ETL Designer
manifest and screen contracts from the controlling
`CDEadmin_First_Party_Module_Specifications` version 1.0 package.

They are embedded so the module registration, screen ownership, commands,
tasks, states and test corpus can be validated without consulting a mutable
runtime source. Product code must not reinterpret these files as provider
support: every live provider capability still requires adapter evidence.
