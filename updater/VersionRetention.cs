// SPDX-License-Identifier: MPL-2.0
using System.Diagnostics;
using System.Text.Json;

namespace SyncBrowser;

public record VersionCleanupEntry(string Directory, long Bytes, string Reason);
public record VersionCleanupReport(VersionCleanupEntry[] Remove, VersionCleanupEntry[] Keep,
                                  VersionCleanupEntry[] Skipped, bool Applied);

internal static class VersionRetention
{
    internal static VersionCleanupReport Clean(Updater updater, bool apply,
                                               IEnumerable<string>? runningExecutables = null)
    {
        var installation = updater.ReadInstallation();
        if (File.GetAttributes(updater.Root).HasFlag(FileAttributes.ReparsePoint))
            throw new InvalidDataException("Installation root is redirected");
        var versions = updater.PathInRoot("versions");
        var protectedFolders = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        foreach (var pointer in new[] { "current.json", "previous.json", "pending.json" })
        {
            if (pointer != "current.json" && !File.Exists(updater.PathInRoot(pointer))) continue;
            var version = updater.ReadVersion(pointer);
            protectedFolders.Add(Path.GetRelativePath(versions, updater.PathInRoot(version.Browser))
                                     .Split(Path.DirectorySeparatorChar)[0]);
        }
        // Abort the entire cleanup if a running browser/helper cannot be inspected.
        foreach (var executable in runningExecutables ?? RunningExecutables())
            if (Path.GetFullPath(executable).StartsWith(versions + Path.DirectorySeparatorChar,
                                                       StringComparison.OrdinalIgnoreCase))
                protectedFolders.Add(Path.GetRelativePath(versions, executable).Split(Path.DirectorySeparatorChar)[0]);
        var remove = new List<VersionCleanupEntry>();
        var keep = new List<VersionCleanupEntry>();
        var skipped = new List<VersionCleanupEntry>();
        foreach (var folder in Directory.EnumerateDirectories(versions))
        {
            var name = Path.GetFileName(folder);
            if (protectedFolders.Contains(name))
            {
                keep.Add(new(name, 0, "Current, previous, pending or running version"));
                continue;
            }
            try
            {
                var safe = updater.PathInRoot("versions/" + name);
                var tag = name;
                if (name.Length > 33 && name[^33] == '-' && Guid.TryParseExact(name[^32..], "N", out _))
                    tag = name[..^33];
                if (!Updater.TagPattern.IsMatch(tag)) throw new InvalidDataException("Unrecognized version directory");
                var manifestPath = updater.PathInRoot("versions/" + name + "/sync-release.json");
                var manifest = JsonSerializer.Deserialize(File.ReadAllText(manifestPath),
                                                          Models.Default.ReleaseManifest);
                if (manifest != new ReleaseManifest(tag, installation.Channel))
                    throw new InvalidDataException("Version identity does not match this installation");
                var files = FilesWithoutRedirects(updater, safe).ToArray();
                var bytes = files.Sum(file => new FileInfo(file).Length);
                if (apply)
                {
                    // Acquire every file before removing anything. A locked payload
                    // is deferred whole, rather than partially deleting an old app.
                    var handles = new List<FileStream>();
                    try
                    {
                        foreach (var file in files)
                        {
                            if (File.GetAttributes(file).HasFlag(FileAttributes.ReadOnly))
                                throw new IOException("Version contains a read-only file; cleanup deferred");
                            handles.Add(new FileStream(file, FileMode.Open, FileAccess.Read, FileShare.Delete));
                        }
                        Directory.Delete(safe, true);
                    }
                    finally { foreach (var handle in handles) handle.Dispose(); }
                }
                remove.Add(new(name, bytes, apply ? "Removed obsolete browser payload" : "Obsolete browser payload"));
            }
            catch (Exception error) when (error is IOException or UnauthorizedAccessException or
                                           InvalidDataException or JsonException)
            {
                skipped.Add(new(name, 0, error.Message));
            }
        }
        return new(remove.ToArray(), keep.ToArray(), skipped.ToArray(), apply);
    }

    private static IEnumerable<string> FilesWithoutRedirects(Updater updater, string directory)
    {
        foreach (var entry in Directory.EnumerateFileSystemEntries(directory))
        {
            updater.PathInRoot(Path.GetRelativePath(updater.Root, entry));
            if (Directory.Exists(entry))
            {
                if (Path.GetFileName(entry).Equals("User Data", StringComparison.OrdinalIgnoreCase))
                    throw new InvalidDataException("Directory contains browser user data");
                foreach (var file in FilesWithoutRedirects(updater, entry)) yield return file;
            }
            else
            {
                if (Path.GetFileName(entry) is "Local State" or "Preferences" or "Cookies" or "Login Data")
                    throw new InvalidDataException("Directory contains browser user data");
                yield return entry;
            }
        }
    }

    private static IEnumerable<string> RunningExecutables()
    {
        if (Environment.ProcessPath is { } current) yield return current;
        foreach (var name in new[] { "brave", "chrome", "SyncUpdater", "SyncBrowser" })
            foreach (var process in Process.GetProcessesByName(name))
                using (process)
                {
                    string? filename;
                    try { filename = process.MainModule?.FileName; }
                    catch (InvalidOperationException) { continue; } // Process already exited.
                    if (filename == null) throw new IOException("A browser or updater process could not be inspected");
                    yield return filename;
                }
    }
}
