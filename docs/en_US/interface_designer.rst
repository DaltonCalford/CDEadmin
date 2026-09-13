.. _interface_designer:

***********************************
Interface and artwork customization
***********************************

The ScratchRobin *Interface Designer* provides one place to configure visual
presentation, artwork assignments, menus, and command presentation. Open it
from *Tools > Interface Designer* or from its workbench activity.

Artwork catalog
===============

CDEadmin-owned artwork is stored under
``web/pgadmin/static/assets/cdeadmin``. The catalog separates product branding,
database engines, command glyphs, and authentication artwork and retains the
applicable licence notices. Features refer to semantic identities such as
``engine.firebird``, ``object.table``, ``tool.query``, or ``action.backup``.
They do not store a physical filename.

The *Artwork & icons* page searches the semantic catalog and assigns another
reviewed catalog entry to a semantic identity. Resetting an assignment restores
the packaged default. Profile files cannot contain raw SVG, arbitrary URLs, or
executable markup.

Appearance and accessibility
============================

The *Appearance & accessibility* page controls the reviewed interface and
monospace fonts; interface and icon scale; line height and letter spacing;
control, target, tree, grid, tab, toolbar, menu, status, scrollbar and resize
geometry; corner radius; active/inactive activity emphasis; motion; density;
and the semantic colour palette.

Numeric values are bounded and unsafe colour combinations are repaired by the
presentation authority. ``Ctrl+Shift+0`` remains the emergency accessibility
safe-mode shortcut. It applies the low-vision, high-contrast, reduced-motion
profile without deleting saved choices.

Menus and commands
==================

The *Menus & commands* page can reorder, rename, decorate, hide, and style the
packaged top-level menus. It can also add or remove user-defined top-level
menus. Registered commands can be placed on a top-level menu and assigned a
label, icon, order, shortcut, font, weight, foreground, background, and icon
position. Hiding or disabling a command adds a personal restriction.

The editor never changes a command's stable identity or executable handler.
It cannot expose a command denied by its permission, security-group, runtime,
provider-capability, or backend authorization checks.

Profiles and deployment defaults
================================

*Save profile* stores the current settings as preferences for the signed-in
account. *Export profile* creates a validated
``cdeadmin.interface-profile.v1`` JSON document, and *Import profile* stages a
validated document for review before it is saved. This supports personal
profiles and reviewable team or corporate baselines.

Administrators can provision the same preference values using CDEadmin's
``preferences.json``/``setup.py set-prefs`` mechanism. The relevant keys are
``browser:interface_customization:icon_assignments``,
``browser:interface_customization:menu_customizations``, and
``browser:commands:command_customizations`` together with the ``misc``
accessibility preferences. Deployment policy and backend authorization remain
authoritative regardless of presentation choices.
