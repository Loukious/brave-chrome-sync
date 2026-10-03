# uBlock Origin installation

Settings > Extensions > Manifest V2 extensions downloads Brave's hosted copies.
That installer sends the compiled `BRAVE_SERVICES_KEY`. The public build has no
private Brave service credentials. A local build without that key reproduced
"Failed to download extension manifest." when `go-updater.brave.com/extensions`
returned HTTP 403. Release branding does not change that download path.

Install uBlock Origin from its author's
[official releases](https://github.com/gorhill/uBlock/releases):

1. Download the Chromium ZIP and extract it to a folder you will keep.
2. Open `brave://extensions` and enable **Developer mode**.
3. Choose **Load unpacked** and select the extracted `uBlock0.chromium` folder
   containing `manifest.json`.

This follows the author's
[manual installation instructions](https://github.com/gorhill/uBlock/blob/master/dist/README.md).
Version 1.75.0 was tested in the local Chromium 155 development build: the
extension was enabled with no manifest or runtime errors. The archive's SHA-256
matched the digest returned by GitHub for the official release asset.

This copy appears on the Extensions page. It uses a different identity from the
Brave-hosted copy and does not enable the Settings toggle. Update it manually by
replacing the files in the same folder with a newer official package, then reload
the extension. Keeping that folder path preserves its settings.
