// SPDX-License-Identifier: MPL-2.0
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Security.Principal;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

namespace SyncBrowser;

internal static class Program
{
    private static string? Option(string[] arguments, string name)
    {
        var joined = arguments.FirstOrDefault(argument => argument.StartsWith(name + "=", StringComparison.Ordinal));
        if (joined != null) return joined[(name.Length + 1)..];
        var index = Array.IndexOf(arguments, name);
        return index >= 0 && index + 1 < arguments.Length ? arguments[index + 1] : null;
    }

    private static string FindRoot(string[] arguments)
    {
        if (Option(arguments, "--root") is { } explicitRoot) return Path.GetFullPath(explicitRoot);
        var beside = Path.Combine(AppContext.BaseDirectory, "sync-install-root.txt");
        return File.Exists(beside) ? Path.GetFullPath(File.ReadAllText(beside).Trim()) : AppContext.BaseDirectory.TrimEnd(Path.DirectorySeparatorChar);
    }

    private static Process? Start(string executable, IEnumerable<string> arguments)
    {
        var start = new ProcessStartInfo(executable) { UseShellExecute = false, CreateNoWindow = true };
        foreach (var argument in arguments) start.ArgumentList.Add(argument);
        return Process.Start(start);
    }

    private static string TaskName(string root)
    {
        if (!OperatingSystem.IsWindows()) throw new PlatformNotSupportedException();
        using var identity = WindowsIdentity.GetCurrent();
        var installId = Convert.ToHexStringLower(SHA256.HashData(Encoding.UTF8.GetBytes(Path.GetFullPath(root).ToUpperInvariant())))[..12];
        return "BraveChromeSync-" + identity.User!.Value + "-" + installId;
    }

    private static async Task ConfigureTask(string root, bool remove)
    {
        var name = TaskName(root);
        var arguments = remove ? new[] { "/Delete", "/TN", name, "/F" } :
            new[] { "/Create", "/TN", name, "/SC", "HOURLY", "/MO", "6", "/RL", "LIMITED", "/F",
                    "/TR", "\"" + Path.Combine(root, "SyncBrowser.exe") + "\" --background" };
        using var process = Start(Path.Combine(Environment.SystemDirectory, "schtasks.exe"), arguments)!;
        await process.WaitForExitAsync();
        if (process.ExitCode != 0 && !remove)
            File.WriteAllText(Path.Combine(root, "scheduled-task-warning.txt"), "Windows declined the background task. Startup and About-page checks remain enabled.");
    }

    private static async Task Initialize(Updater updater, string[] arguments)
    {
        var tag = Option(arguments, "--tag") ?? throw new ArgumentException("Missing release tag");
        var channel = Option(arguments, "--channel") ?? throw new ArgumentException("Missing channel");
        var browser = Option(arguments, "--browser") ?? throw new ArgumentException("Missing browser path");
        if (!Updater.TagPattern.IsMatch(tag) || channel is not ("nightly" or "beta" or "release"))
            throw new ArgumentException("Invalid installation identity");
        var exe = updater.PathInRoot(browser);
        var app = Path.GetDirectoryName(exe)!;
        var manifest = JsonSerializer.Deserialize(File.ReadAllText(Path.Combine(app, "sync-release.json")), Models.Default.ReleaseManifest);
        if (manifest != new ReleaseManifest(tag, channel) || !File.Exists(exe) || !File.Exists(Path.Combine(app, "SyncUpdater.exe")))
            throw new InvalidDataException("Installer payload is incomplete");
        using (var updateLock = await updater.LockAsync(TimeSpan.FromSeconds(30)))
        {
            if (File.Exists(updater.PathInRoot("installation.json")) && updater.ReadInstallation().Channel != channel)
                throw new InvalidDataException("Install another channel in a separate directory");
            Updater.AtomicWrite(updater.PathInRoot("installation.json"), new Installation(Updater.Repository, channel), Models.Default.Installation);
            File.WriteAllText(Path.Combine(app, "sync-install-root.txt"), updater.Root);
            var version = new InstalledVersion(tag, browser.Replace('\\', '/'), Option(arguments, "--published") ?? "1970-01-01T00:00:00Z");
            if (!File.Exists(updater.PathInRoot("current.json")))
                Updater.AtomicWrite(updater.PathInRoot("current.json"), version, Models.Default.InstalledVersion);
            else if (Updater.Compare(tag, updater.ReadVersion().Tag) >= 0 && tag != updater.ReadVersion().Tag)
                Updater.AtomicWrite(updater.PathInRoot("pending.json"), version, Models.Default.InstalledVersion);
        }
        await updater.ActivateAsync();
        await ConfigureTask(updater.Root, false);
    }

    public static async Task<int> Main(string[] arguments)
    {
        string? statusFile = Option(arguments, "--status-file");
        string? root = null;
        try
        {
            root = FindRoot(arguments);
            using var http = new HttpClient { Timeout = TimeSpan.FromMinutes(30) };
            http.DefaultRequestHeaders.UserAgent.ParseAdd("BraveChromeSync-Updater/1");
            http.DefaultRequestHeaders.Accept.ParseAdd("application/vnd.github+json");
            var updater = new Updater(root, http);
            if (arguments.Contains("--initialize")) { await Initialize(updater, arguments); return 0; }
            if (arguments.Contains("--uninstall")) { await ConfigureTask(root, true); return 0; }
            updater.ReadInstallation();

            // The stable shortcut target delegates to the updater in the current version.
            var currentUpdater = Path.Combine(Path.GetDirectoryName(updater.PathInRoot(updater.ReadVersion().Browser))!, "SyncUpdater.exe");
            if (!Path.GetFullPath(Environment.ProcessPath!).Equals(currentUpdater, StringComparison.OrdinalIgnoreCase))
            {
                var commands = new[] { "--check", "--background", "--relaunch" };
                var forwarded = commands.Any(arguments.Contains) ? arguments : new[] { "--" }.Concat(arguments);
                using var delegated = Start(currentUpdater, new[] { "--root", root }.Concat(forwarded))!;
                if (arguments.Contains("--check")) { await delegated.WaitForExitAsync(); return delegated.ExitCode; }
                return 0;
            }

            if (arguments.Contains("--check") || arguments.Contains("--background"))
            {
                void Report(UpdateStatus status)
                {
                    if (statusFile != null) Updater.AtomicWrite(statusFile, status, Models.Default.UpdateStatus);
                }
                if (arguments.Contains("--background"))
                {
                    var stamp = updater.PathInRoot("background-check.txt");
                    if (File.Exists(stamp) && DateTime.UtcNow - File.GetLastWriteTimeUtc(stamp) < TimeSpan.FromHours(5)) return 0;
                    File.WriteAllText(stamp, DateTimeOffset.UtcNow.ToString("O"));
                }
                Report(new("checking"));
                await updater.CheckAsync(Report);
                return 0;
            }
            var separator = Array.IndexOf(arguments, "--");
            var browserArguments = separator >= 0 ? arguments.Skip(separator + 1).ToArray() : arguments;
            if (arguments.Contains("--relaunch"))
            {
                var pid = int.Parse(Option(arguments, "--parent-pid") ?? throw new ArgumentException("Missing parent process"));
                try { using var parent = Process.GetProcessById(pid); await parent.WaitForExitAsync().WaitAsync(TimeSpan.FromMinutes(2)); }
                catch (ArgumentException) { }
                var deadline = DateTime.UtcNow + TimeSpan.FromMinutes(2);
                while (updater.BrowserIsRunning() && DateTime.UtcNow < deadline) await Task.Delay(250);
            }
            await updater.ActivateAsync();
            using var browserProcess = updater.StartBrowser(browserArguments);
            using var background = Start(currentUpdater, new[] { "--background", "--root", root });
            return 0;
        }
        catch (Exception exception)
        {
            if (statusFile != null)
            {
                try { Updater.AtomicWrite(statusFile, new UpdateStatus("failed", Message: exception.Message, Done: true), Models.Default.UpdateStatus); }
                catch (IOException) { }
            }
            if (root != null && Directory.Exists(root))
                try { File.WriteAllText(Path.Combine(root, "updater-error.txt"), DateTimeOffset.UtcNow.ToString("O") + " " + exception); }
                catch (IOException) { }
            if (!arguments.Contains("--background") && !arguments.Contains("--check") && OperatingSystem.IsWindows())
                MessageBox(IntPtr.Zero, exception.Message, "Brave Chrome Sync", 0x10);
            return 1;
        }
    }

    [DllImport("user32.dll", CharSet = CharSet.Unicode, EntryPoint = "MessageBoxW")]
    private static extern int MessageBox(IntPtr window, string text, string caption, uint type);
}
