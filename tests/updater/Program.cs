using System.IO.Compression;
using System.Net;
using System.Security.Cryptography;
using System.Text.Json;
using SyncBrowser;
using Microsoft.Win32;

const string oldTag = "v1.99.8-sync.r1.0123456789ab";
const string newTag = "v1.99.9-sync.r2.abcdef012345";
var testRoot = Path.GetFullPath(Path.Combine("work", "native-updater-tests-" + Guid.NewGuid().ToString("N")));
Directory.CreateDirectory(testRoot);
int passed = 0;

void Assert(bool condition, string message)
{
    if (!condition) throw new Exception(message);
}

async Task Test(string name, Func<Task> test)
{
    await test();
    Console.WriteLine("PASS " + name);
    passed++;
}

async Task Reject(Func<Task> action)
{
    try { await action(); }
    catch (InvalidDataException) { return; }
    throw new Exception("Expected update rejection");
}

byte[] Payload(string tag = newTag, string extra = "", bool omitBrowser = false)
{
    using var memory = new MemoryStream();
    using (var archive = new ZipArchive(memory, ZipArchiveMode.Create, true))
    {
        var entries = new Dictionary<string, string> {
            ["chrome.dll"] = "fixture", ["SyncUpdater.exe"] = "fixture",
            ["sync-release.json"] = JsonSerializer.Serialize(new ReleaseManifest(tag, "nightly"), Models.Default.ReleaseManifest)
        };
        if (!omitBrowser) entries["brave.exe"] = "fixture";
        if (extra != "") entries[extra] = "unsafe";
        foreach (var (name, content) in entries)
        {
            using var stream = new StreamWriter(archive.CreateEntry(name).Open());
            stream.Write(content);
        }
    }
    return memory.ToArray();
}

object Release(string tag, byte[] payload, string channel = "nightly", bool draft = false, string? url = null)
{
    string Asset(string name) => $"https://github.com/{Updater.Repository}/releases/download/{tag}/{name}";
    var name = $"brave-chrome-sync-{tag}-windows-x64.zip";
    return new { tag_name = tag, name = $"Brave Chrome Sync [{channel}] {tag.Split("-sync.")[0]}",
        draft, prerelease = channel != "release", published_at = "2026-10-03T12:00:00Z",
        assets = new[] {
            new { name, browser_download_url = url ?? Asset(name), size = payload.Length, digest = "sha256:" + Convert.ToHexStringLower(SHA256.HashData(payload)) },
            new { name = "SHA256SUMS", browser_download_url = Asset("SHA256SUMS"), size = 1, digest = "" },
            new { name = $"brave-chrome-sync-{tag}-windows-x64-setup.exe", browser_download_url = Asset("setup.exe"), size = 1, digest = "" }
        }
    };
}

(Updater Updater, FakeGitHub GitHub) Fixture(string name, byte[] payload, string tag = newTag, string? url = null)
{
    var root = Path.Combine(testRoot, name);
    Directory.CreateDirectory(Path.Combine(root, "versions", "old"));
    File.WriteAllText(Path.Combine(root, "versions", "old", "brave.exe"), "fixture");
    Updater.AtomicWrite(Path.Combine(root, "installation.json"), new Installation(Updater.Repository, "nightly"), Models.Default.Installation);
    Updater.AtomicWrite(Path.Combine(root, "current.json"), new InstalledVersion(oldTag, "versions/old/brave.exe", "2026-10-01T00:00:00Z"), Models.Default.InstalledVersion);
    var github = new FakeGitHub(JsonSerializer.Serialize(new[] { Release(tag, payload, url: url) }), payload,
        Convert.ToHexStringLower(SHA256.HashData(payload)) + $"  brave-chrome-sync-{tag}-windows-x64.zip\n");
    return (new Updater(root, new HttpClient(github)), github);
}

await Test("channel, draft and numeric version selection", () => {
    var payload = Payload();
    using var document = JsonDocument.Parse(JsonSerializer.Serialize(new[] {
        Release("v1.99.10-sync.r1.0123456789ab", payload), Release(newTag, payload),
        Release("v1.100.0-sync.r1.0123456789ab", payload, draft: true),
        Release("v1.101.0-sync.r1.0123456789ab", payload, "beta")
    }));
    Assert(Updater.SelectRelease(document.RootElement, "nightly")!.Value.GetProperty("tag_name").GetString() == "v1.99.10-sync.r1.0123456789ab", "Wrong channel or lexical sort");
    return Task.CompletedTask;
});

await Test("stage, restart activation and rollback pointer", async () => {
    var (updater, github) = Fixture("success", Payload());
    var statuses = new List<UpdateStatus>();
    await updater.CheckAsync(statuses.Add);
    Assert(updater.ReadVersion().Tag == oldTag, "Changed running version before relaunch");
    Assert(updater.ReadVersion("pending.json").Tag == newTag && statuses.Last().Status == "ready", "No ready update");
    Assert(statuses.Any(s => s.Status == "updating"), "No download progress");
    await updater.CheckAsync(statuses.Add);
    Assert(github.Downloads == 1, "Downloaded an already staged update again");
    await updater.ActivateAsync();
    Assert(updater.ReadVersion().Tag == newTag && updater.ReadVersion("previous.json").Tag == oldTag, "Activation did not retain previous version");
    await updater.CheckAsync(statuses.Add);
    Assert(statuses.Last().Status == "updated", "Current version not reported as up to date");
});

await Test("missing installed browser explains how to diagnose quarantine", () => {
    var (updater, _) = Fixture("quarantined", Payload());
    var browser = updater.PathInRoot(updater.ReadVersion().Browser);
    File.Delete(browser);
    try { updater.ReadVersion(); }
    catch (InvalidDataException error)
    {
        Assert(error.Message.Contains(browser) && error.Message.Contains("Windows Security"),
               "Missing browser was reported as an invalid version without recovery guidance");
        Assert(File.Exists(updater.PathInRoot("current.json")), "Missing executable removed the installation pointer");
        return Task.CompletedTask;
    }
    throw new Exception("Missing installed browser was accepted");
});

await Test("bad digest leaves current installation intact", async () => {
    var (updater, github) = Fixture("digest", Payload());
    github.Payload = (byte[])github.Payload.Clone();
    github.Payload[0] ^= 1;
    await Reject(() => updater.CheckAsync(_ => { }));
    Assert(updater.ReadVersion().Tag == oldTag && !File.Exists(updater.PathInRoot("pending.json")), "Tampered update became active");
});

foreach (var entry in new[] { "../escape.exe", "/absolute.exe", "C:/escape.exe", "data:stream", "folder/../../escape.exe", "BRAVE.EXE", "folder/.. /escape.exe", "NUL.txt" })
    await Test("unsafe archive rejected: " + entry, async () => {
        var (updater, _) = Fixture("unsafe-" + passed, Payload(extra: entry));
        await Reject(() => updater.CheckAsync(_ => { }));
        Assert(!File.Exists(updater.PathInRoot("pending.json")), "Unsafe payload staged");
    });

await Test("payload release identity must match", async () => {
    var (updater, _) = Fixture("identity", Payload(oldTag));
    await Reject(() => updater.CheckAsync(_ => { }));
});

await Test("missing browser rejected", async () => {
    var (updater, _) = Fixture("missing", Payload(omitBrowser: true));
    await Reject(() => updater.CheckAsync(_ => { }));
});

await Test("downgrade refused", async () => {
    var (updater, github) = Fixture("downgrade", Payload(), "v1.99.7-sync.r9.abcdef012345");
    UpdateStatus? status = null;
    await updater.CheckAsync(value => status = value);
    Assert(status!.Status == "updated" && github.Downloads == 0 && updater.ReadVersion().Tag == oldTag, "Downgrade downloaded");
});

await Test("foreign asset URL refused before download", async () => {
    var (updater, github) = Fixture("foreign", Payload(), url: "https://github.com/other/repo/releases/download/evil/payload.zip");
    await Reject(() => updater.CheckAsync(_ => { }));
    Assert(github.Downloads == 0, "Fetched an unrelated asset");
});

await Test("update lock serializes checks", async () => {
    var (updater, github) = Fixture("lock", Payload());
    using var held = await updater.LockAsync(TimeSpan.Zero);
    var checking = updater.CheckAsync(_ => { });
    await Task.Delay(250);
    Assert(github.Requests == 0, "Update bypassed installation lock");
    held.Dispose();
    await checking;
});

await Test("packaged Google configuration reaches the browser environment", () => {
    var start = new System.Diagnostics.ProcessStartInfo("fixture");
    foreach (var name in GoogleClientConfiguration.Variables) start.Environment.Remove(name);
    GoogleClientConfiguration.Apply(start, new("fixture-api", "fixture-client", "fixture-secret"));
    Assert(start.Environment["GOOGLE_API_KEY"] == "fixture-api" &&
           start.Environment["GOOGLE_DEFAULT_CLIENT_ID"] == "fixture-client" &&
           start.Environment["GOOGLE_DEFAULT_CLIENT_SECRET"] == "fixture-secret", "Packaged configuration was not forwarded");
    return Task.CompletedTask;
});

await Test("explicit complete Google configuration remains unchanged", () => {
    var start = new System.Diagnostics.ProcessStartInfo("fixture");
    foreach (var name in GoogleClientConfiguration.Variables) start.Environment[name] = "custom-" + name;
    GoogleClientConfiguration.Apply(start, new("fixture-api", "fixture-client", "fixture-secret"));
    Assert(GoogleClientConfiguration.Variables.All(name => start.Environment[name] == "custom-" + name), "Explicit runtime configuration changed");
    return Task.CompletedTask;
});

await Test("missing configuration fails before launching a browser", () => {
    var start = new System.Diagnostics.ProcessStartInfo("fixture");
    foreach (var name in GoogleClientConfiguration.Variables) start.Environment.Remove(name);
    try { GoogleClientConfiguration.Apply(start, new("", "", "")); }
    catch (InvalidDataException) { return Task.CompletedTask; }
    throw new Exception("Missing configuration could sign users out");
});

await Test("a custom OAuth client cannot use an unrelated default secret", () => {
    var start = new System.Diagnostics.ProcessStartInfo("fixture");
    foreach (var name in GoogleClientConfiguration.Variables) start.Environment.Remove(name);
    start.Environment["GOOGLE_DEFAULT_CLIENT_ID"] = "custom-client";
    try { GoogleClientConfiguration.Apply(start, new("fixture-api", "fixture-client", "fixture-secret")); }
    catch (InvalidDataException) { return Task.CompletedTask; }
    throw new Exception("A mismatched OAuth client was accepted");
});

string AddVersion(Updater updater, string suffix, string tag = newTag)
{
    var folder = updater.PathInRoot("versions/" + tag + "-" + suffix);
    Directory.CreateDirectory(folder);
    File.WriteAllText(Path.Combine(folder, "brave.exe"), "fixture");
    File.WriteAllText(Path.Combine(folder, "chrome.dll"), "fixture");
    File.WriteAllText(Path.Combine(folder, "sync-release.json"),
                      JsonSerializer.Serialize(new ReleaseManifest(tag, "nightly"), Models.Default.ReleaseManifest));
    return folder;
}

await Test("retention protects pointers and running payloads, then removes only obsolete versions", () => {
    var (updater, _) = Fixture("retention", Payload());
    var previous = AddVersion(updater, new string('a', 32));
    var pending = AddVersion(updater, new string('b', 32));
    var running = AddVersion(updater, new string('c', 32));
    var obsolete = AddVersion(updater, new string('d', 32));
    InstalledVersion Pointer(string folder) => new(newTag, Path.GetRelativePath(updater.Root, Path.Combine(folder, "brave.exe")).Replace('\\', '/'), "2026-10-03T00:00:00Z");
    Updater.AtomicWrite(updater.PathInRoot("previous.json"), Pointer(previous), Models.Default.InstalledVersion);
    Updater.AtomicWrite(updater.PathInRoot("pending.json"), Pointer(pending), Models.Default.InstalledVersion);
    var runningExecutables = new[] { Path.Combine(running, "SyncUpdater.exe") };
    var plan = VersionRetention.Clean(updater, false, runningExecutables);
    Assert(plan.Remove.Length == 1 && plan.Remove[0].Directory == Path.GetFileName(obsolete), "Dry run included a protected payload");
    Assert(Directory.Exists(obsolete) && plan.Keep.Length == 4, "Dry run changed files or missed a protected version");
    var applied = VersionRetention.Clean(updater, true, runningExecutables);
    Assert(applied.Remove.Length == 1 && !Directory.Exists(obsolete), "Obsolete payload was not pruned");
    Assert(new[] { previous, pending, running }.All(Directory.Exists) && File.Exists(updater.PathInRoot(updater.ReadVersion().Browser)), "A protected version was removed");
    return Task.CompletedTask;
});

await Test("retention refuses unknown folders, wrong identities and browser user data", () => {
    var (updater, _) = Fixture("retention-identity", Payload());
    var unknown = updater.PathInRoot("versions/not-a-version");
    Directory.CreateDirectory(unknown);
    var foreign = AddVersion(updater, new string('a', 32));
    File.WriteAllText(Path.Combine(foreign, "sync-release.json"), "{\"release_tag\":\"foreign\",\"channel\":\"nightly\",\"protocol\":1}");
    var profile = AddVersion(updater, new string('b', 32));
    File.WriteAllText(Path.Combine(profile, "Local State"), "profile sentinel");
    var report = VersionRetention.Clean(updater, true, []);
    Assert(report.Remove.Length == 0 && report.Skipped.Length == 3, "Unrecognized data was selected for pruning");
    Assert(new[] { unknown, foreign, profile }.All(Directory.Exists), "Foreign or profile data was deleted");
    return Task.CompletedTask;
});

await Test("retention refuses redirected directories and redirected files", () => {
    var (updater, _) = Fixture("retention-links", Payload());
    var outside = AddVersion(updater, new string('a', 32));
    var linked = updater.PathInRoot("versions/" + newTag + "-" + new string('b', 32));
    Directory.CreateSymbolicLink(linked, outside);
    var inside = AddVersion(updater, new string('c', 32));
    File.CreateSymbolicLink(Path.Combine(inside, "redirect"), Path.Combine(outside, "brave.exe"));
    // Protect the target as though an updater is still running there.
    var report = VersionRetention.Clean(updater, true, [Path.Combine(outside, "SyncUpdater.exe")]);
    Assert(report.Remove.Length == 0 && report.Skipped.Length == 2 && File.Exists(Path.Combine(outside, "brave.exe")), "Cleanup followed a redirect");
    return Task.CompletedTask;
});

await Test("retention defers a locked payload before removing any files", () => {
    var (updater, _) = Fixture("retention-locked", Payload());
    var obsolete = AddVersion(updater, new string('a', 32));
    using var held = new FileStream(Path.Combine(obsolete, "chrome.dll"), FileMode.Open, FileAccess.ReadWrite, FileShare.None);
    var report = VersionRetention.Clean(updater, true, []);
    Assert(report.Remove.Length == 0 && report.Skipped.Length == 1 && File.Exists(Path.Combine(obsolete, "brave.exe")), "Cleanup partially removed a locked payload");
    return Task.CompletedTask;
});

if (OperatingSystem.IsWindows())
await Test("registration migrates this install's duplicate, preserves default aliases and leaves other Brave installs", () => {
    var (updater, _) = Fixture("registration-migration", Payload());
    File.WriteAllText(updater.PathInRoot("SyncBrowser.exe"), "fixture");
    using var user = Registry.CurrentUser.CreateSubKey(@"Software\BraveChromeSync\RegistrationTests\" + Guid.NewGuid().ToString("N"));
    void Set(string path, string name, string value) { using var key = user.CreateSubKey(path); key.SetValue(name, value); }
    string? Get(string path, string name) { using var key = user.OpenSubKey(path); return key?.GetValue(name) as string; }
    const string legacy = @"Software\Clients\StartMenuInternet\Brave.fixture";
    const string other = @"Software\Clients\StartMenuInternet\Brave.other";
    Set(@"Software\RegisteredApplications", "Brave.fixture", legacy + @"\Capabilities");
    Set(@"Software\RegisteredApplications", "Brave.other", other + @"\Capabilities");
    Set(legacy + @"\shell\open\command", "", "\"" + updater.PathInRoot(updater.ReadVersion().Browser) + "\"");
    Set(other + @"\shell\open\command", "", "\"C:\\Other Brave\\brave.exe\"");
    Set(legacy + @"\Capabilities\URLAssociations", "http", "BraveHTML.fixture");
    Set(@"Software\Classes\BraveHTML.fixture\shell\open\command", "", "\"" + updater.PathInRoot(updater.ReadVersion().Browser) + "\" \"%1\"");
    BrowserRegistration.Register(updater, user);
    Assert(Get(@"Software\RegisteredApplications", "Brave.fixture") == null, "Owned duplicate remains advertised");
    Assert(Get(@"Software\RegisteredApplications", "Brave.other") == other + @"\Capabilities", "Another Brave installation was changed");
    Assert(Get(@"Software\RegisteredApplications", "Brave Chrome Sync Nightly") != null, "Canonical app registration is missing");
    Assert(Get(@"Software\Classes\BraveHTML.fixture\shell\open\command", "") == "\"" + updater.PathInRoot("SyncBrowser.exe") + "\" \"%1\"", "Existing default alias was removed or bypasses the launcher");
    BrowserRegistration.Register(updater, user);
    Assert(Get(@"Software\RegisteredApplications", "Brave.fixture") == null, "Registration migration is not idempotent");
    return Task.CompletedTask;
});

Console.WriteLine($"{passed} updater scenarios passed. Fixtures retained at {testRoot}");

sealed class FakeGitHub(string releases, byte[] payload, string sums) : HttpMessageHandler
{
    public byte[] Payload { get; set; } = payload;
    public int Downloads { get; private set; }
    public int Requests { get; private set; }
    protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
    {
        Requests++;
        HttpContent content;
        if (request.RequestUri!.Host == "api.github.com") content = new StringContent(releases);
        else if (request.RequestUri.AbsolutePath.EndsWith("SHA256SUMS")) content = new StringContent(sums);
        else { Downloads++; content = new ByteArrayContent(Payload); }
        return Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK) { Content = content });
    }
}
