# Approved application artwork

`approved-icon.png` is the unchanged supplied original from
`verified-approved-app-icons.zip`. Its filename and SHA-256 are recorded in
`provenance.json`. No alternate artwork is used.

With Pillow 11.3.0 installed, run `python scripts/generate_icons.py` from
this application directory. `--check` verifies the source hash and every
committed derivative. The complete square image and original alpha channel
are retained: only LANCZOS resizing is applied, with no crop, recoloring,
padding, compositing or additional mask. The ICO contains 16/32/48px sizes.
The manifest deliberately uses `purpose: any` rather than requesting a
platform mask that could crop the approved composition.

HTML and manifests use the same relative `/icons/approved-v1/` paths on
local and remote hosts. No proxy, origin, authentication or application
configuration changes are required. Existing installed shortcuts may need
to be removed and added again because iOS keeps its own saved icon.

Deployment gate: verify all three application implementations and CI before
updating any add-on. Live local/remote equality is a separate runtime check;
repository tests establish handler equality, not deployed proxy behavior.
