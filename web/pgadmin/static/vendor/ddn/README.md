# DDN libraries vendored by CDEadmin

CDEadmin consumes these files as independently reusable libraries. They are
not CDEadmin's authoritative model implementation and must not be modified to
create application-specific behavior.

Versions:

- DDN runtime/viewer: `0.5.0-draft.2`
- DDN Designer: `0.1.0-preview.1`

The Designer requires the exact supplied runtime. Product code must use the
public module declarations and the CDEadmin adapter under
`cdeadmin_ui/integrations/ddn`; it must not import internal helpers, manipulate
the Designer shadow DOM, or treat rendered SVG as authoritative data.

The source distribution identifies the libraries as MIT licensed. The
original SPDX notices are retained in the distributed files and the MIT text
is included in `LICENSE-MIT.txt`.

`resource-manifest.json` records the provenance and hashes of the exact files
accepted into this repository. The two `ddn.global.js` files are identical by
design because each upstream distribution is self-contained.
