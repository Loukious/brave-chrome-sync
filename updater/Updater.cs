// SPDX-License-Identifier: MPL-2.0
using System.Diagnostics;
using System.IO.Compression;
using System.Net.Http.Headers;
using System.Security.Cryptography;
using System.Text.Json;
using System.Text.Json.Serialization;
using System.Text.Json.Serialization.Metadata;
using System.Text.RegularExpressions;

namespace SyncBrowser;

public record Installation(string Repository, string Channel, int Protocol = 1);
public record InstalledVersion(string Tag, string Browser, string PublishedAt);
public record UpdateStatus(string Status, int Progress = 0, string Version = "",
                           string Message = "", bool Done = false);
public record ReleaseManifest(string ReleaseTag, string Channel, int Protocol = 1);

[JsonSourceGenerationOptions(PropertyNamingPolicy = JsonKnownNamingPolicy.SnakeCaseLower,
                            WriteIndented = true)]
[JsonSerializable(typeof(Installation))]
[JsonSerializable(typeof(InstalledVersion))]
[JsonSerializable(typeof(UpdateStatus))]
[JsonSerializable(typeof(ReleaseManifest))]
public partial class Models : JsonSerializerContext { }

public sealed class Updater(string root, HttpClient http)
{
    public const string Repository = "Loukious/brave-chrome-sync";
    public string Root { get; } = Path.GetFullPath(root);
    public static readonly Regex TagPattern = new(@"^v(\d+)\.(\d+)\.(\d+)-sync\.r(\d+)\.([a-f0-9]{12})$",
                                                RegexOptions.CultureInvariant);

    public string PathInRoot(string relative)
    {
        if (string.IsNullOrWhiteSpace(relative) || Path.IsPathRooted(relative) ||
            relative.IndexOfAny([':', '<', '>', '|', '?', '*', '\0']) >= 0 ||
            relative.Split('/', '\\').Any(p => p.Length == 0 || p.EndsWith('.') || p.EndsWith(' ') ||
                Regex.IsMatch(p, @"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(\.|$)", RegexOptions.IgnoreCase)))
            throw new InvalidDataException("Invalid installation path");
        var path = Path.GetFullPath(Path.Combine(Root, relative));
        if (!path.StartsWith(Root + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase))
            throw new InvalidDataException("Path leaves the installation");
        for (var current = path; current != null && current.Length > Root.Length;
             current = Path.GetDirectoryName(current))
            if ((File.Exists(current) || Directory.Exists(current)) &&
                File.GetAttributes(current).HasFlag(FileAttributes.ReparsePoint))
                throw new InvalidDataException("Installation contains a redirected path");
        return path;
    }

    public Installation ReadInstallation()
    {
        var installation = JsonSerializer.Deserialize(File.ReadAllText(PathInRoot("installation.json")), Models.Default.Installation)
                           ?? throw new InvalidDataException("Missing installation");
        if (installation.Repository != Repository || installation.Protocol != 1 ||
            installation.Channel is not ("nightly" or "beta" or "release"))
            throw new InvalidDataException("Unsupported installation identity");
        return installation;
    }

    public InstalledVersion ReadVersion(string name = "current.json")
    {
        var version = JsonSerializer.Deserialize(File.ReadAllText(PathInRoot(name)), Models.Default.InstalledVersion)
                      ?? throw new InvalidDataException("Missing installed version");
        if (!TagPattern.IsMatch(version.Tag) || !version.Browser.StartsWith("versions/", StringComparison.Ordinal))
            throw new InvalidDataException("Invalid installed version");
        var browser = PathInRoot(version.Browser);
        if (!File.Exists(browser))
            throw new InvalidDataException($"Installed browser executable is missing: {browser}. " +
                "Check Windows Security protection history for a quarantine, then repair the installation using its setup installer.");
        return version;
    }

    public static void AtomicWrite<T>(string path, T value, JsonTypeInfo<T> type)
    {
        var temporary = path + "." + Guid.NewGuid().ToString("N") + ".tmp";
        File.WriteAllText(temporary, JsonSerializer.Serialize(value, type));
        File.Move(temporary, path, true);
    }

    public async Task<FileStream> LockAsync(TimeSpan timeout)
    {
        var deadline = DateTime.UtcNow + timeout;
        while (true)
        {
            try { return new FileStream(PathInRoot("update.lock"), FileMode.OpenOrCreate, FileAccess.ReadWrite, FileShare.None); }
            catch (IOException) when (DateTime.UtcNow < deadline) { await Task.Delay(200); }
        }
    }

    public static int Compare(string a, string b)
    {
        var left = TagPattern.Match(a);
        var right = TagPattern.Match(b);
        if (!left.Success || !right.Success) throw new InvalidDataException("Invalid release tag");
        for (var i = 1; i <= 4; i++)
        {
            var result = long.Parse(left.Groups[i].Value).CompareTo(long.Parse(right.Groups[i].Value));
            if (result != 0) return result;
        }
        return 0;
    }

    private static bool IsNewer(InstalledVersion candidate, InstalledVersion installed) =>
        Compare(candidate.Tag, installed.Tag) > 0 ||
        (Compare(candidate.Tag, installed.Tag) == 0 && candidate.Tag != installed.Tag &&
         DateTimeOffset.Parse(candidate.PublishedAt) > DateTimeOffset.Parse(installed.PublishedAt));

    private static Uri AssetUri(JsonElement asset, string tag)
    {
        var uri = new Uri(asset.GetProperty("browser_download_url").GetString()!);
        var prefix = $"/{Repository}/releases/download/{tag}/";
        if (uri.Scheme != "https" || uri.Host != "github.com" || !uri.AbsolutePath.StartsWith(prefix, StringComparison.Ordinal) ||
            !string.IsNullOrEmpty(uri.UserInfo) || !string.IsNullOrEmpty(uri.Query))
            throw new InvalidDataException("Release asset is outside this repository");
        return uri;
    }

    public static JsonElement? SelectRelease(JsonElement releases, string channel)
    {
        JsonElement? best = null;
        foreach (var release in releases.EnumerateArray())
        {
            var tag = release.GetProperty("tag_name").GetString()!;
            if (release.GetProperty("draft").GetBoolean() || !TagPattern.IsMatch(tag) ||
                release.GetProperty("name").GetString() != $"Brave Chrome Sync [{channel}] {tag.Split("-sync.")[0]}" ||
                (channel == "release" && release.GetProperty("prerelease").GetBoolean())) continue;
            if (best == null || Compare(tag, best.Value.GetProperty("tag_name").GetString()!) > 0 ||
                (Compare(tag, best.Value.GetProperty("tag_name").GetString()!) == 0 &&
                 DateTimeOffset.Parse(release.GetProperty("published_at").GetString()!) >
                 DateTimeOffset.Parse(best.Value.GetProperty("published_at").GetString()!))) best = release;
        }
        return best;
    }

    public async Task CheckAsync(Action<UpdateStatus> report)
    {
        using var updateLock = await LockAsync(TimeSpan.FromMinutes(35));
        var installation = ReadInstallation();
        var installed = ReadVersion();
        report(new("checking"));
        using var response = await http.GetAsync($"https://api.github.com/repos/{Repository}/releases?per_page=100");
        response.EnsureSuccessStatusCode();
        using var document = JsonDocument.Parse(await response.Content.ReadAsStringAsync());
        var selected = SelectRelease(document.RootElement, installation.Channel);
        if (selected is null) throw new InvalidDataException("No published release for this channel");
        var release = selected.Value;
        var tag = release.GetProperty("tag_name").GetString()!;
        var published = release.GetProperty("published_at").GetString()!;
        var candidate = new InstalledVersion(tag, "", published);
        InstalledVersion? pending = File.Exists(PathInRoot("pending.json")) ? ReadVersion("pending.json") : null;
        if (pending != null && pending.Tag == tag)
        {
            report(new("ready", 100, tag, Done: true));
            return;
        }
        if (!IsNewer(candidate, installed) || (pending != null && !IsNewer(candidate, pending)))
        {
            report(pending != null ? new("ready", 100, pending.Tag, Done: true) : new("updated", Done: true));
            return;
        }
        var name = $"brave-chrome-sync-{tag}-windows-x64.zip";
        var assets = release.GetProperty("assets").EnumerateArray().ToArray();
        var asset = assets.Single(a => a.GetProperty("name").GetString() == name);
        _ = assets.Single(a => a.GetProperty("name").GetString() == $"brave-chrome-sync-{tag}-windows-x64-setup.exe");
        var uri = AssetUri(asset, tag);
        var sums = assets.Single(a => a.GetProperty("name").GetString() == "SHA256SUMS");
        var checksums = await http.GetStringAsync(AssetUri(sums, tag));
        var checksum = checksums.Split('\n').Select(line => line.TrimEnd('\r')).Single(line => line.EndsWith("  " + name, StringComparison.Ordinal)).Split("  ")[0];
        if (!Regex.IsMatch(checksum, "^[a-f0-9]{64}$")) throw new InvalidDataException("Missing SHA-256 digest");
        if (asset.TryGetProperty("digest", out var digest) && digest.ValueKind == JsonValueKind.String &&
            digest.GetString() != "sha256:" + checksum) throw new InvalidDataException("Conflicting release digests");
        var identifier = tag + "-" + Guid.NewGuid().ToString("N");
        var download = PathInRoot(identifier + ".zip");
        using (var payload = await http.GetAsync(uri, HttpCompletionOption.ResponseHeadersRead))
        {
            payload.EnsureSuccessStatusCode();
            await using var input = await payload.Content.ReadAsStreamAsync();
            await using var output = new FileStream(download, FileMode.CreateNew, FileAccess.Write, FileShare.None);
            var expectedSize = asset.GetProperty("size").GetInt64();
            if (expectedSize is <= 0 or > 4L * 1024 * 1024 * 1024) throw new InvalidDataException("Invalid payload size");
            var buffer = new byte[131072];
            long total = 0;
            int lastPercent = -1;
            while (true)
            {
                var count = await input.ReadAsync(buffer);
                if (count == 0) break;
                total += count;
                if (total > expectedSize) throw new InvalidDataException("Payload exceeds declared size");
                await output.WriteAsync(buffer.AsMemory(0, count));
                var percent = (int)(total * 90 / expectedSize);
                if (percent != lastPercent) { report(new("updating", percent, tag)); lastPercent = percent; }
            }
            if (total != expectedSize) throw new InvalidDataException("Incomplete payload");
        }
        using (var stream = File.OpenRead(download))
            if (Convert.ToHexStringLower(await SHA256.HashDataAsync(stream)) != checksum)
                throw new InvalidDataException("Downloaded update failed SHA-256 verification");
        report(new("updating", 95, tag));
        var folder = "versions/" + identifier;
        Extract(download, PathInRoot(folder));
        var browsers = Directory.GetFiles(PathInRoot(folder), "*.exe", SearchOption.AllDirectories)
            .Where(p => Path.GetFileName(p).Equals("brave.exe", StringComparison.OrdinalIgnoreCase) ||
                        Path.GetFileName(p).Equals("chrome.exe", StringComparison.OrdinalIgnoreCase)).ToArray();
        if (browsers.Length != 1) throw new InvalidDataException("Missing or ambiguous browser executable");
        var app = Path.GetDirectoryName(browsers[0])!;
        var manifest = JsonSerializer.Deserialize(File.ReadAllText(Path.Combine(app, "sync-release.json")), Models.Default.ReleaseManifest);
        if (manifest != new ReleaseManifest(tag, installation.Channel) ||
            !File.Exists(Path.Combine(app, "SyncUpdater.exe")) || !File.Exists(Path.Combine(app, "chrome.dll")))
            throw new InvalidDataException("Update payload identity or updater is missing");
        File.WriteAllText(Path.Combine(app, "sync-install-root.txt"), Root);
        var browserRelative = Path.GetRelativePath(Root, browsers[0]).Replace('\\', '/');
        AtomicWrite(PathInRoot("pending.json"), new InstalledVersion(tag, browserRelative, published), Models.Default.InstalledVersion);
        AtomicWrite(PathInRoot("last-check.json"), new UpdateStatus("checked", Message: DateTimeOffset.UtcNow.ToString("O"), Done: true), Models.Default.UpdateStatus);
        // Only our verified disposable download is removed; installed versions remain for rollback.
        File.Delete(download);
        report(new("ready", 100, tag, Done: true));
    }

    public static void Extract(string archive, string destination)
    {
        Directory.CreateDirectory(destination);
        using var client = new HttpClient();
        var validator = new Updater(destination, client);
        using var zip = ZipFile.OpenRead(archive);
        var names = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        long size = 0;
        foreach (var entry in zip.Entries)
        {
            var name = entry.FullName.Replace('\\', '/');
            var path = validator.PathInRoot(name.TrimEnd('/'));
            if (!names.Add(path) || ((entry.ExternalAttributes >> 16) & 0xf000) == 0xa000 ||
                (size += entry.Length) > 8L * 1024 * 1024 * 1024)
                throw new InvalidDataException("Unsafe archive entry");
            if (name.EndsWith('/')) { Directory.CreateDirectory(path); continue; }
            Directory.CreateDirectory(Path.GetDirectoryName(path)!);
            entry.ExtractToFile(path, false);
        }
    }

    public bool BrowserIsRunning()
    {
        foreach (var name in new[] { "brave", "chrome" })
            foreach (var process in Process.GetProcessesByName(name))
                using (process)
                {
                    try
                    {
                        var path = process.MainModule?.FileName;
                        if (path != null && path.StartsWith(PathInRoot("versions") + Path.DirectorySeparatorChar,
                                                          StringComparison.OrdinalIgnoreCase)) return true;
                    }
                    catch (System.ComponentModel.Win32Exception) { return true; }
                    catch (InvalidOperationException) { }
                }
        return false;
    }

    public async Task ActivateAsync()
    {
        using var updateLock = await LockAsync(TimeSpan.FromSeconds(30));
        ReadInstallation();
        if (!File.Exists(PathInRoot("pending.json")) || BrowserIsRunning()) return;
        var pending = ReadVersion("pending.json");
        var current = ReadVersion();
        if (!IsNewer(pending, current)) throw new InvalidDataException("Refusing an update downgrade");
        AtomicWrite(PathInRoot("previous.json"), current, Models.Default.InstalledVersion);
        AtomicWrite(PathInRoot("current.json"), pending, Models.Default.InstalledVersion);
        File.Delete(PathInRoot("pending.json"));
    }

    public Process StartBrowser(IEnumerable<string> arguments)
    {
        var start = new ProcessStartInfo(PathInRoot(ReadVersion().Browser)) { UseShellExecute = false };
        foreach (var argument in arguments.Where(a => !a.StartsWith("--wait-for-parent-handle", StringComparison.Ordinal)))
            start.ArgumentList.Add(argument);
        if (!start.ArgumentList.Any(a => a.StartsWith("--user-data-dir", StringComparison.Ordinal)))
            start.ArgumentList.Add("--user-data-dir=" + Path.Combine(
                Environment.GetEnvironmentVariable("LOCALAPPDATA") is { Length: > 0 } local ? local :
                    Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "BraveChromeSync", "User Data"));
        return Process.Start(start) ?? throw new IOException("Could not start the browser");
    }
}
