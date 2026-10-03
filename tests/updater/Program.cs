using System.IO.Compression;
using System.Net;
using System.Security.Cryptography;
using System.Text.Json;
using SyncBrowser;

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
