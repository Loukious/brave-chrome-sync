// SPDX-License-Identifier: MPL-2.0
using Microsoft.Win32;
using System.Runtime.InteropServices;
using System.Runtime.Versioning;

namespace SyncBrowser;

[SupportedOSPlatform("windows")]
internal static class BrowserRegistration
{
    private static readonly string[] Extensions = [".htm", ".html", ".shtml", ".xht", ".xhtml", ".mht", ".mhtml", ".pdf", ".svg", ".webp"];
    [DllImport("shell32.dll")]
    private static extern void SHChangeNotify(uint eventId, uint flags, IntPtr item, IntPtr other);

    private static string Name(string channel) => channel == "release" ? "Brave Chrome Sync" : "Brave Chrome Sync " + (channel == "beta" ? "Beta" : "Nightly");
    private static string Client(string channel) => @"Software\Clients\StartMenuInternet\BraveChromeSync-" + channel;
    private static string ProgId(string channel, string kind) => "BraveChromeSync." + channel + "." + kind;
    public static void Register(Updater updater, RegistryKey? registry = null)
    {
        var user = registry ?? Registry.CurrentUser;
        void Set(string path, string name, string value)
        {
            using var key = user.CreateSubKey(path);
            key.SetValue(name, value, RegistryValueKind.String);
        }
        var channel = updater.ReadInstallation().Channel;
        var name = Name(channel);
        var client = Client(channel);
        var launcher = updater.PathInRoot("SyncBrowser.exe");
        if (!File.Exists(launcher)) throw new InvalidDataException("Browser launcher is missing");
        var browser = updater.PathInRoot(updater.ReadVersion().Browser);
        var icon = "\"" + browser + "\",0";
        var command = "\"" + launcher + "\" \"%1\"";
        var capabilities = client + @"\Capabilities";
        Set(client, "", name);
        Set(client, "InstallRoot", updater.Root);
        Set(client + @"\DefaultIcon", "", icon);
        Set(client + @"\shell\open\command", "", "\"" + launcher + "\"");
        Set(capabilities, "ApplicationName", name);
        Set(capabilities, "ApplicationDescription", "Browse the web with Brave Chrome Sync.");
        Set(capabilities, "ApplicationIcon", icon);
        Set(capabilities + @"\StartMenu", "StartMenuInternet", "BraveChromeSync-" + channel);
        foreach (var kind in new[] { "HTML", "URL" })
        {
            var progId = ProgId(channel, kind);
            var path = @"Software\Classes\" + progId;
            Set(path, "", name + (kind == "HTML" ? " HTML Document" : " URL"));
            if (kind == "URL") Set(path, "URL Protocol", "");
            Set(path + @"\DefaultIcon", "", icon);
            Set(path + @"\shell\open\command", "", command);
            Set(path + @"\Application", "ApplicationName", name);
            Set(path + @"\Application", "ApplicationDescription", "Browse the web with Brave Chrome Sync.");
            Set(path + @"\Application", "ApplicationIcon", icon);
            Set(path + @"\Application", "AppUserModelID", "Loukious.BraveChromeSync." + channel);
        }
        foreach (var extension in Extensions)
        {
            Set(capabilities + @"\FileAssociations", extension, ProgId(channel, "HTML"));
            using var openWith = user.CreateSubKey(@"Software\Classes\" + extension + @"\OpenWithProgids");
            openWith.SetValue(ProgId(channel, "HTML"), Array.Empty<byte>(), RegistryValueKind.None);
        }
        foreach (var protocol in new[] { "http", "https" })
            Set(capabilities + @"\URLAssociations", protocol, ProgId(channel, "URL"));
        Set(@"Software\RegisteredApplications", name, capabilities);
        MigrateLegacyRegistrations(updater, user, client, command, icon, name);
        SHChangeNotify(0x08000000, 0, IntPtr.Zero, IntPtr.Zero);
    }

    private static bool OwnsCommand(Updater updater, string? command)
    {
        if (command == null || !command.StartsWith('"')) return false;
        var end = command.IndexOf('"', 1);
        if (end < 0 || (end + 1 < command.Length && !char.IsWhiteSpace(command[end + 1]))) return false;
        try
        {
            var executable = Path.GetFullPath(command[1..end]);
            return executable.Equals(updater.PathInRoot("SyncBrowser.exe"), StringComparison.OrdinalIgnoreCase) ||
                (executable.StartsWith(updater.PathInRoot("versions") + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase) &&
                 Path.GetFileName(executable).ToLowerInvariant() is "brave.exe" or "chrome.exe");
        }
        catch (Exception error) when (error is ArgumentException or InvalidDataException or NotSupportedException) { return false; }
    }

    private static void MigrateLegacyRegistrations(Updater updater, RegistryKey user, string canonicalClient,
                                                   string command, string icon, string name)
    {
        using var registered = user.OpenSubKey(@"Software\RegisteredApplications", true);
        if (registered == null) return;
        const string prefix = @"Software\Clients\StartMenuInternet\";
        foreach (var application in registered.GetValueNames())
        {
            if (registered.GetValue(application) is not string capabilities ||
                !capabilities.StartsWith(prefix + "Brave", StringComparison.OrdinalIgnoreCase) ||
                !capabilities.EndsWith(@"\Capabilities", StringComparison.OrdinalIgnoreCase)) continue;
            var client = capabilities[..^13];
            if (client.Equals(canonicalClient, StringComparison.OrdinalIgnoreCase) ||
                client[prefix.Length..].Contains('\\')) continue;
            using var openCommand = user.OpenSubKey(client + @"\shell\open\command");
            if (!OwnsCommand(updater, openCommand?.GetValue("") as string)) continue;
            // Preserve existing UserChoice ProgIDs as stable aliases. Removing
            // them would invalidate a user's previously selected default.
            foreach (var associations in new[] { "URLAssociations", "FileAssociations" })
            {
                using var values = user.OpenSubKey(capabilities + "\\" + associations);
                if (values == null) continue;
                foreach (var association in values.GetValueNames())
                {
                    if (values.GetValue(association) is not string progId || progId.Contains('\\')) continue;
                    var path = @"Software\Classes\" + progId;
                    using var handler = user.OpenSubKey(path + @"\shell\open\command", true);
                    if (!OwnsCommand(updater, handler?.GetValue("") as string)) continue;
                    handler!.SetValue("", command);
                    using var app = user.CreateSubKey(path + @"\Application");
                    app.SetValue("ApplicationName", name);
                    app.SetValue("ApplicationIcon", icon);
                    using var defaultIcon = user.CreateSubKey(path + @"\DefaultIcon");
                    defaultIcon.SetValue("", icon);
                }
            }
            registered.DeleteValue(application, false);
            user.DeleteSubKeyTree(client, false);
        }
    }

    public static void Unregister(Updater updater)
    {
        var channel = updater.ReadInstallation().Channel;
        var client = Client(channel);
        using (var owner = Registry.CurrentUser.OpenSubKey(client))
            if (owner?.GetValue("InstallRoot") is not string root || !root.Equals(updater.Root, StringComparison.OrdinalIgnoreCase)) return;
        using (var registered = Registry.CurrentUser.OpenSubKey(@"Software\RegisteredApplications", true))
            if (registered?.GetValue(Name(channel)) as string == client + @"\Capabilities") registered.DeleteValue(Name(channel), false);
        foreach (var extension in Extensions)
        {
            using var openWith = Registry.CurrentUser.OpenSubKey(@"Software\Classes\" + extension + @"\OpenWithProgids", true);
            openWith?.DeleteValue(ProgId(channel, "HTML"), false);
        }
        Registry.CurrentUser.DeleteSubKeyTree(client, false);
        foreach (var kind in new[] { "HTML", "URL" })
            Registry.CurrentUser.DeleteSubKeyTree(@"Software\Classes\" + ProgId(channel, kind), false);
        SHChangeNotify(0x08000000, 0, IntPtr.Zero, IntPtr.Zero);
    }
}
