# GitHub browser updates on Windows

Install the `-windows-x64-setup.exe` asset from a completed release. The per-user
installer creates Start Menu shortcuts, an optional desktop shortcut, an
uninstaller, and an update task that runs every six hours. It needs no elevation,
PowerShell installation, .NET installation, or external update server.

The default installation is `%LOCALAPPDATA%\Programs\BraveChromeSync-release`.
The stable shortcut target is `SyncBrowser.exe`. It delegates to the updater
bundled with the selected browser version, so future releases can update the
helper without replacing a running launcher. The installer has its own AppId
and shortcut identity. The default profile is
`%LOCALAPPDATA%\BraveChromeSync\User Data`, retained across updates and uninstall.

## About page and background checks

The Windows `VersionUpdater` override connects `brave://settings/help` to
GitHub Releases. It reports Chromium's existing Checking, Updating, Up to date,
failure, and Relaunch states, including download progress. Blocking network,
extraction and file operations run in the separate helper; the browser polls
its status on a worker thread.

Browser startup also starts a quiet check, throttled to once every five hours.
The six-hour Windows scheduled task checks while the browser is closed or open.
If Windows policy declines task creation, startup and About checks still work;
`scheduled-task-warning.txt` records that condition. Checks use public GitHub
API access and can temporarily fail because of connectivity or GitHub rate
limits. Such failures leave the installed browser usable, and About can retry.

Updates are selected from published releases of `Loukious/brave-chrome-sync`
for the installed channel. Nightly and Beta are kept separate from stable.
Numeric browser version and patch revision take precedence; publication time
orders rebuilds of the same version/revision. Drafts, foreign asset URLs and
version downgrades are rejected. Every ZIP must match `SHA256SUMS`; GitHub's
asset digest must agree when present. The embedded release manifest must match
the selected tag, channel and updater protocol. ZIP traversal, duplicate names,
Windows device paths and links are rejected.

## Installing a staged update

The helper extracts a verified ZIP into a new `versions/` directory. It writes
`pending.json` only after the browser, updater and release manifest validate.
The active `current.json` remains untouched while the browser runs. About then
shows Relaunch.

Chromium's Windows relaunch hook invokes the helper with the parent process and
restart arguments. The helper waits for the browser to exit, retains
`previous.json`, atomically selects the pending version, and starts that version
with the same profile and session-restoration arguments. Starting a shortcut
after closing the browser also activates a staged update. A running browser
under this installation prevents activation. The helper never forcibly closes
the user's browser.

Old versions remain available for manual recovery. There is no automatic health
rollback or pruning yet. For recovery, close this installation's browser and
restore `current.json` from `previous.json`; preserve the profile. The installer
uninstaller removes installed application versions and its update task, while
retaining the separate profile.

## Build and validation

The fifth and sixth patches in `patches/series` contain the native browser
hooks, while `updater/` contains the self-contained .NET helper and `installer/`
contains the Inno Setup installer. `scripts/package_windows.py` builds both
release assets and pins the compiler download to a verified SHA-256.

The build uses Release configuration, `is_official_build=true`,
`is_debug=false`, non-component binaries and orange release branding. Both the
Windows release and basic updater entry points use our GitHub implementation.
`enable_updater=false` and
`enable_update_notifications=false` disable Brave's Omaha browser updater.
Chromium component updates remain separate. An Omaha URL cannot consume
GitHub's release JSON directly; this helper implements that translation locally.

The independent build sets `require_brave_service_keys=false`; private Brave
backend credentials are optional during compilation. Services that require
those credentials still require valid keys at runtime. PGO, LTO and symbol
archives are disabled to keep the release build within available resources.

Pull-request checks test channel/version selection, checksum failure, archive
validation, staging/activation, locking and downgrade prevention. A Windows
fixture test compiles the production installer, installs it, checks the Start
Menu shortcut, and verifies launcher arguments and profile preservation.
Full release builds additionally start both the packaged browser and the
installed browser through the stable launcher, checking their DevTools endpoints.
CI refuses publication if either startup check fails.

The binaries are currently unsigned. Integrity depends on HTTPS and control of
the GitHub repository; checksums are not an independent publisher signature.
Authenticode signing can be added when a signing identity is available.
