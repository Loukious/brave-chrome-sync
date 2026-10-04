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
    private static void Set(string path, string name, string value)
    {
        using var key = Registry.CurrentUser.CreateSubKey(path);
        key.SetValue(name, value, RegistryValueKind.String);
    }

    public static void Register(Updater updater)
    {
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
            using var openWith = Registry.CurrentUser.CreateSubKey(@"Software\Classes\" + extension + @"\OpenWithProgids");
            openWith.SetValue(ProgId(channel, "HTML"), Array.Empty<byte>(), RegistryValueKind.None);
        }
        foreach (var protocol in new[] { "http", "https" })
            Set(capabilities + @"\URLAssociations", protocol, ProgId(channel, "URL"));
        Set(@"Software\RegisteredApplications", name, capabilities);
        SHChangeNotify(0x08000000, 0, IntPtr.Zero, IntPtr.Zero);
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
