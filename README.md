# Brave Chrome Sync

Windows x64 builds of Brave with Loukious's Chromium Google sign-in and Sync
patches. The original patch set was tested successfully with Google Sync.
This is an independent build and release repository, not an official Brave
distribution.

The source checkout used to create the patches was Brave Nightly **v1.99.8**
(`9cc0476e9506f93ff6921b0afed668d048f07313`). All four local commits are
preserved in `patches/series`, including the Windows patch-parser and Wintun
build fixes. The fifth and sixth patches add GitHub updates to the Windows
browser in both release and local build configurations. The seventh lets this
independent release build omit unused Brave backend keys.
Google credentials and local browser profiles are excluded. The Brave client
service key is supplied through an Actions secret and compiled into the browser.

## Automated builds

Every day at 04:23 UTC, GitHub Actions finds the newest published version on
the configured Brave source channel. The source default is **Nightly**, matching
the patch base. Our browser ships as **Release**, with orange release branding,
the full version number, `is_official_build=true`, and non-component binaries.
Source selection follows Brave Nightly, independently of our release branding.
Updates of the patch/build scripts also trigger a build. You can select
a specific published upstream tag with **Actions → Sync Brave, build Windows
x64, release → Run workflow**.

The workflow:

1. Resolves the upstream tag to an immutable `brave-core` commit.
2. Replays every patch on a fresh checkout. A conflict fails before the browser
   build starts; patches are never silently omitted.
3. Initializes the matching Chromium source and Brave dependencies.
4. Builds Windows x64 across up to 16 sequential jobs, each compiling for up to
   210 minutes and then checkpointing the initialized source and build output.
5. Builds the bundled updater and Windows installer, then starts both the
   packaged and installed browser headlessly and checks their DevTools endpoints.
6. Checks archive integrity, SHA-256 checksums, and source provenance, then
   publishes the completed release. Failed or unfinished builds produce no
   public release.

The controller repository stays small. Upstream Brave and Chromium are fetched
at build time, with the maintained patch series applied on top. There is no
second full Chromium repository to push or merge. The source for each shipped
Brave build is included as `patched-brave-core.tar.gz`; the matching Chromium
version, upstream commit, patch digest, and build settings are recorded in
`build-metadata.json`.

Release tags look like `v1.99.9-sync.r2.0123456789ab`. The suffix identifies the
patch revision and a digest of the build inputs. Completed identical builds are
skipped, and published release assets are never overwritten. `release_channel`
controls our installer, update feed and GitHub prerelease flag independently
of `upstream_channel`, which selects Brave source versions.

## Resource limits

The design follows the sequential checkpoint approach in
[Veil-Chromium](https://github.com/Maishan-Inc/Veil-Chromium) and
[ungoogled-chromium-windows](https://github.com/ungoogled-software/ungoogled-chromium-windows).
It uses two compile workers, one linker, no debug symbols, no PGO/LTO, and the
portable ZIP compilation target, then packages a small independent Inno Setup
installer; it avoids Chromium's full installer and symbol archive targets.
Compilation output is retained between stages at the same `D:\b` path, including
source timestamps and pnpm symlinks, so later jobs can continue the build.
Checkpoint creation streams through a binary pipe to avoid Windows tar's
incorrect archive-self file matches. Tar warnings fail the checkpoint, and
vendored Cargo file checksums are validated before archiving and after restore.

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
more free disk. The first `windows-2025-vs2026` run showed approximately 29 GiB
free on C: and 220 GiB on D:, so the build and its caches use D:. This is an
observed runner allocation, not a guaranteed capacity. Initialization,
compilation, or checkpoint compression may
exceed available disk, memory, or the six-hour budget. Full-source checkpoints
can be large, and Actions artifact storage can incur charges beyond your
account's allowance. No billing settings or paid runners are enabled by this
repository. Check your existing GitHub budget and storage limits before
relying on recurring builds.

The default runner is `windows-2025-vs2026`, because current Chromium requires
Visual Studio 2026. Set `runner` in `config.json` to a provisioned Windows runner
label if hosted capacity proves insufficient. A larger runner requires a
GitHub billing configuration; a self-hosted runner requires its own machine.
The checkpoint workflow expects fresh runners with no pre-existing `D:\b`.
No host caches, installed toolchains, or existing output directories are removed
to gain space.

## Install and update from GitHub

Download and run **`brave-chrome-sync-<tag>-windows-x64-setup.exe`** from
[Releases](https://github.com/Loukious/brave-chrome-sync/releases).
It installs for your Windows account, adds Start Menu shortcuts and an optional
desktop shortcut, and enables GitHub updates. No administrator privileges,
PowerShell installation, or .NET installation are needed.

Open **About Brave** (`brave://settings/help`) to check and download updates.
The page shows progress and then **Relaunch**. Relaunch selects the staged
version after the browser exits, preserving your profile and session.
Startup and six-hour background checks also stage updates automatically.
If Windows declines the scheduled task, startup and About checks remain active.

The installer uses `%LOCALAPPDATA%\Programs\BraveChromeSync-release` by default
and keeps an independent profile at `%LOCALAPPDATA%\BraveChromeSync\User Data`.
Updates use verified ZIP payloads from this repository's published releases,
install versions side by side, retain the previous version, and never replace
the running executable. The profile is retained across updates and uninstall.
The binaries are currently unsigned.

For portable use, extract the release ZIP manually, or use PowerShell 7 and
the two optional scripts included with the release:

```powershell
pwsh -File .\Update-BraveChromeSync.ps1
pwsh -File .\Start-BraveChromeSync.ps1
```

The PowerShell portable launcher checks at launch and falls back to the installed
version when offline. The Windows installer provides the About-page experience;
portable copies without an installer manifest use the script-based update path.
For custom portable repositories or locations:

```powershell
pwsh -File .\Update-BraveChromeSync.ps1 -Repository owner/repo -InstallRoot D:\Browser
pwsh -File .\Start-BraveChromeSync.ps1 -InstallRoot D:\Browser
```

The installed updater stays on this repository and its installation channel.
Brave's Omaha browser updater is disabled through explicit GN arguments.
Component updates, such as filter lists, are separate from browser executable
updates. See [update behavior and recovery](docs/UPDATING.md).

The uBlock Origin toggle in Settings uses Brave's signed package and original
extension ID. Builds require the `BRAVE_SERVICES_KEY` Actions secret; the workflow
checks the authenticated manifest before compilation and again before packaging.
See [extension service configuration](docs/EXTENSIONS.md).

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
dotnet run --project tests/updater/Updater.Tests.csproj -p:PublishSingleFile=false -p:PublishTrimmed=false
```

Pull requests run inexpensive automation tests, native helper and installer
tests, and patch replay against the documented base. Release builds run from `main`; untrusted pull requests
cannot publish or access build credentials. Actions are pinned to commit SHAs.

## Credits and license

Brave is licensed under MPL-2.0, and Chromium and third-party components retain
their respective licenses. This repository preserves Brave's `LICENSE` and
the authorship in the exported patches. The automation was written for this
project; the linked build workflows informed the staged-build design. No code
from their JavaScript build actions was copied. See [CREDITS.md](CREDITS.md).
