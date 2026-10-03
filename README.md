# Brave Chrome Sync

Windows x64 builds of Brave with Loukious's Chromium Google sign-in and Sync
patches. The original patch set was tested successfully with Google Sync.
This is an independent build and release repository, not an official Brave
distribution.

The source checkout used to create the patches was Brave Nightly **v1.99.8**
(`9cc0476e9506f93ff6921b0afed668d048f07313`). All four local commits are
preserved in `patches/series`, including the Windows patch-parser and Wintun
build fixes. No credentials or local browser profiles are included.

## Automated builds

Every day at 04:23 UTC, GitHub Actions finds the newest published version on
the configured Brave channel. The default is **Nightly**, matching the patch
base. Updates of the patch/build scripts also trigger a build. You can select
a specific published upstream tag with **Actions → Sync Brave, build Windows
x64, release → Run workflow**.

The workflow:

1. Resolves the upstream tag to an immutable `brave-core` commit.
2. Replays every patch on a fresh checkout. A conflict fails before the browser
   build starts; patches are never silently omitted.
3. Initializes the matching Chromium source and Brave dependencies.
4. Builds Windows x64 across up to 16 sequential jobs, each compiling for up to
   210 minutes and then checkpointing the initialized source and build output.
5. Starts the packaged browser headlessly and checks its DevTools endpoint.
6. Checks archive integrity, SHA-256 checksums, and source provenance, then
   publishes the completed release. Failed or unfinished builds produce no
   public release.

The controller repository stays small. Upstream Brave and Chromium are fetched
at build time, with the maintained patch series applied on top. There is no
second full Chromium repository to push or merge. The source for each shipped
Brave build is included as `patched-brave-core.tar.gz`; the matching Chromium
version, upstream commit, patch digest, and build settings are recorded in
`build-metadata.json`.

Release tags look like `v1.99.9-sync.r1.0123456789ab`. The suffix identifies the
patch revision and a digest of the build inputs. Completed identical builds are
skipped, and published release assets are never overwritten. Nightly and Beta
are explicitly marked as prereleases, even if upstream incorrectly marks a
Nightly release as stable.

## Resource limits

The design follows the sequential checkpoint approach in
[Veil-Chromium](https://github.com/Maishan-Inc/Veil-Chromium) and
[ungoogled-chromium-windows](https://github.com/ungoogled-software/ungoogled-chromium-windows).
It uses two compile workers, one linker, no debug symbols, no PGO/LTO, and the
portable ZIP target rather than building symbol archives and installers.
Compilation output is retained between stages at the same `C:\b` path, including
source timestamps and pnpm symlinks, so later jobs can continue the build.

Standard hosted runner compute is
[free for public repositories](https://docs.github.com/en/actions/reference/runners/github-hosted-runners).
[Each hosted job is limited to six hours](https://docs.github.com/en/actions/reference/limits).
Checkpoint transfer and source initialization consume part of that budget.
Consumed checkpoints are deleted only after the next artifact is uploaded;
the current checkpoint remains available if the next stage fails. All artifacts
expire after five days, and successful publication removes the final artifact.

**This does not guarantee that Brave fits on a free runner.** Brave has a larger
dependency tree than ungoogled Chromium. GitHub documents 16 GB RAM and 14 GB
SSD storage for standard public Windows runners; a particular image may offer
more free disk. Initialization, compilation, or checkpoint compression may
exceed available disk, memory, or the six-hour budget. Full-source checkpoints
can be large, and Actions artifact storage can incur charges beyond your
account's allowance. No billing settings or paid runners are enabled by this
repository. Check your existing GitHub budget and storage limits before
relying on recurring builds.

The default runner is `windows-2025-vs2026`, because current Chromium requires
Visual Studio 2026. Set `runner` in `config.json` to a provisioned Windows runner
label if hosted capacity proves insufficient. A larger runner requires a
GitHub billing configuration; a self-hosted runner requires its own machine.
The checkpoint workflow expects fresh runners with no pre-existing `C:\b`.
No host caches, installed toolchains, or existing output directories are removed
to gain space.

## Install and update from GitHub

The release contains an unsigned **portable ZIP**, not an installer. You can
extract it and launch the included browser manually, or use PowerShell 7 and
the two scripts included with the release:

```powershell
pwsh -File .\Update-BraveChromeSync.ps1
pwsh -File .\Start-BraveChromeSync.ps1
```

The launcher checks GitHub for an update on each start. It selects a completed
release for the same channel, verifies the browser ZIP against GitHub's
SHA-256 asset digest, installs into a new version directory, and switches its
small `current.json` pointer after extraction succeeds. It retains previous
versions, refuses browser version downgrades, and defers updating while this
browser is running. An unavailable network falls back to an installed version.
Partial downloads never replace the current browser. Staging files are retained
for diagnosis; they can be removed manually once no update is running.

The launcher keeps an independent profile at
`%LOCALAPPDATA%\BraveChromeSync\User Data`. It does not modify an existing Brave
installation or register Brave's native updater. Launch through this script
to get GitHub update checks; double-clicking the browser executable bypasses
them. A persistent background update service is not installed.

For a custom repository or install location, use updater parameters:

```powershell
pwsh -File .\Update-BraveChromeSync.ps1 -Repository owner/repo -InstallRoot D:\Browser
pwsh -File .\Start-BraveChromeSync.ps1 -InstallRoot D:\Browser
```

After the first install the launcher uses the repository saved in `current.json`.
Use a different install root when changing channels or repositories. The native
Brave browser updater is disabled through explicit GN arguments, so it cannot
replace the customized browser with stock Brave. Component updates, such as
filter lists, are separate from browser executable updating.

See [native updater feasibility](docs/UPDATING.md) for integration options.

## Google credentials

Use the same working credentials you tested, supplied **at runtime**:

```powershell
$env:GOOGLE_API_KEY = 'your-working-api-key'
$env:GOOGLE_DEFAULT_CLIENT_ID = 'your-working-client-id'
$env:GOOGLE_DEFAULT_CLIENT_SECRET = 'your-working-client-secret'
pwsh -File .\Start-BraveChromeSync.ps1
```

Do not commit these values. The public build intentionally embeds no Google
credentials, and checkpoint artifacts contain none. Runtime variables are
supported by [Chromium](https://www.chromium.org/developers/how-tos/api-keys/).
The original successful Sync test validates the starting patch set; every new
upstream version should still be checked with your account. The headless CI
startup test does not sign in to a Google account.

## Maintain the patches

Patches target **brave-core**, including its `chromium_src`, `patches`, and
`rewrite` customizations. They are applied before `pnpm run init`, which applies
the resulting Brave customizations to the matching Chromium checkout.

When upstream changes conflict, update the affected patch against the selected
version, preserve its attribution, and keep the intended order in
`patches/series`. Bump `patch_revision` for a deliberate rebuild; changing patch
or build inputs also changes the release digest. To track stable Brave, change
`upstream_channel` to `release` and update the minimum version and patch base
after porting the patches to that branch. A stable branch older than the patch
base is rejected by scheduled selection.

Change configuration and regenerate the controller workflow:

```powershell
python scripts/generate_workflow.py
python scripts/generate_workflow.py --check
python -m unittest discover -s tests -v
```

Pull requests run only inexpensive automation tests and patch replay against
the documented base. Release builds run from `main`; untrusted pull requests
cannot publish or access build credentials. Actions are pinned to commit SHAs.

## Credits and license

Brave is licensed under MPL-2.0, and Chromium and third-party components retain
their respective licenses. This repository preserves Brave's `LICENSE` and
the authorship in the exported patches. The automation was written for this
project; the linked build workflows informed the staged-build design. No code
from their JavaScript build actions was copied. See [CREDITS.md](CREDITS.md).
