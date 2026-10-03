# Updating the customized browser

## Current implementation

The CI build passes `enable_updater=false` and
`enable_update_notifications=false` as explicit GN arguments. Brave's build
configuration forces its updater on for Release builds before it merges extra
GN arguments, so a `.env` setting alone is insufficient; this pipeline's
explicit arguments take precedence.

The portable GitHub updater is independent of the browser executable. It uses
the GitHub Releases API, requires the API-provided SHA-256 digest for the ZIP,
checks the archive, and installs versions side by side. The launcher checks on
startup. Integrity is rooted in HTTPS and the selected GitHub repository; this
does not add an independent publisher signature. There is no background
service and `brave://settings/help` does not install GitHub releases.

Browser executable updates and Chromium component updates are distinct. The
pipeline disables the native browser updater, not the component update client.

## Why changing an endpoint is insufficient

At the patch base, `chromium_src/chrome/updater/branding.gni` declares
`update_check_url` as
`https://updates.bravesoftware.com/prod/service/update2/json`.
That is an Omaha protocol endpoint, not a list of releases. The updater expects
an application-specific update response, package metadata, install actions,
and the associated updater trust configuration. GitHub's release JSON does not
have that contract. Pointing `update_check_url` at GitHub's API will therefore
not make the native updater work.

References:

- [Brave updater branding at the patch base](https://github.com/brave/brave-core/blob/v1.99.8/chromium_src/chrome/updater/branding.gni)
- [Chromium updater documentation](https://chromium.googlesource.com/chromium/src/+/main/docs/updater/)
- [Chromium updater protocol 3.1](https://chromium.googlesource.com/chromium/src/+/main/docs/updater/protocol_3_1.md)
- [Chromium updater protocol 4](https://chromium.googlesource.com/chromium/src/+/main/docs/updater/protocol_4.md)

## Options for native integration

An Omaha adapter can discover completed GitHub releases and return the
protocol's expected response while the actual installers are hosted as GitHub
assets. This needs a server endpoint, separate application and updater identity
(GUIDs, registry paths, service names, profile directories, and branding),
appropriate package/CUP trust configuration, and signed Windows installers.
Brave's prebuilt updater cannot be treated as a customized updater merely by
changing its server URL. End-to-end installation, replacement, downgrade,
failure recovery, and signature validation would need testing.

Another option is a new GitHub-aware browser updater/helper. That needs native
UI integration, process exit handling, locked-file replacement, trust/signature
verification, and recovery. It is substantially more browser code and testing
than the portable launcher implemented here.

For this resource-constrained pipeline, the portable launcher avoids a server
and administrator service installation and delivers GitHub update checks now.
Native updater integration remains separate future work.
