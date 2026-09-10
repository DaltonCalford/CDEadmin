.. _project_governance:

*****************************
Repository and fork governance
*****************************

CDEadmin is independently developed from the pgAdmin 4 9.17 source baseline.
It does not track an upstream remote, accept automatic upstream merges, submit
changes to pgAdmin, or promise source compatibility. The retained commit
history, licence and copyright notices provide provenance and attribution.

Git and GitHub relationships
============================

A local clone is independent when its only remote targets the CDEadmin
repository. GitHub's *fork network* flag is separate hosting metadata. Renaming
a fork or changing a Git remote does not remove that flag. The repository owner
can use *Settings -> General -> Danger Zone -> Leave fork network* when the
repository meets GitHub's eligibility rules. At this checkpoint the public
CDEadmin repository is below 1 GB and has no child forks, so those published
rules are satisfied. This action is permanent. Git commit metadata is retained,
but GitHub warns that issues, pull requests, wikis, stars, watchers, comments,
child forks and other hosted metadata are not retained. Back up any required
hosted metadata before detaching. Deleting and manually recreating the
repository is the fallback procedure, not the preferred one.

Detachment must not remove attribution, rewrite inherited copyright history or
replace the PostgreSQL Licence. After detachment, ``git remote -v`` must still
show only the independently controlled CDEadmin origin.

Compatibility identifiers
=========================

The inherited Python package ``pgadmin``, selected JavaScript and route names,
database migrations and compatibility environment variables remain where an
immediate rename would break data or extension compatibility. They are internal
compatibility boundaries rather than evidence of an upstream integration.
New project-owned interfaces use CDEadmin naming.

Historical documentation
========================

The repository retains upstream release notes and PostgreSQL-provider chapters
where they remain legally or technically useful. These documents are labelled
as inherited material. Current product behavior is defined by the CDEadmin
overview, architecture, connector, interface and implementation-status pages
and by the exact provider contracts.
