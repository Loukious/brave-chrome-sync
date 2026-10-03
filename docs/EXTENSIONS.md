# Native extension installation

Settings > Extensions > Manifest V2 extensions uses Brave's original installer,
signed CRX packages and extension IDs. uBlock Origin keeps the ID
`jcokkipkhhgiakinbnnplhkdbjbgcgpe`. Users enable it through Settings and accept
the browser's normal permission prompt.

The installer sends the compiled `BRAVE_SERVICES_KEY` to
`https://go-updater.brave.com/extensions`. An empty value caused HTTP 403 with
the response `Missing auth header`. Twenty requests with different User-Agents,
channel values and empty versus omitted key headers all produced that response.
Using the official client's header returned HTTP 200 and the matching uBlock
manifest. This fixes the missing build configuration without changing the ID.

For this personal build, the owner configured the client service key from their
installed official Brave 1.96.61 / Chromium 154 browser as the repository's
`BRAVE_SERVICES_KEY` Actions secret. Its value is not committed or printed by our
scripts. It is compiled into distributed browser binaries, as in official Brave.
Forks need to configure their own build's secret before running the workflow.

The workflow checks the manifest before source preparation. Packaging verifies
the generated build flag matches the configured key and rechecks the endpoint.
An authentication failure stops publication. These checks verify service access;
they do not claim that a native permission prompt was accepted in the headless
startup test.

`brave_services_key_id` defaults to Brave's platform, Chromium major and channel.
Its default is unchanged. This ID is used for Brave's separate HTTP signature
code; the extension installer itself sends `BraveServiceKey` and does not require
a signature or key ID.

Brave documents its build parameters in
[Build configuration](https://github.com/brave/brave-browser/wiki/Build-configuration).
