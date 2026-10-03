// SPDX-License-Identifier: MPL-2.0
using System.Runtime.InteropServices;
using System.Runtime.Versioning;

namespace SyncBrowser;

[SupportedOSPlatform("windows")]
internal static unsafe class TaskbarPins
{
    [StructLayout(LayoutKind.Sequential)]
    private struct PropertyKey(Guid format, uint id) { public Guid Format = format; public uint Id = id; }
    [StructLayout(LayoutKind.Explicit, Size = 24)]
    private struct Variant { [FieldOffset(0)] public ushort Type; [FieldOffset(8)] public IntPtr Pointer; }
    [DllImport("ole32.dll")]
    private static extern int CoInitializeEx(IntPtr reserved, uint mode);
    [DllImport("ole32.dll")]
    private static extern void CoUninitialize();
    [DllImport("ole32.dll", PreserveSig = false)]
    private static extern void CoCreateInstance(ref Guid clsid, IntPtr outer, uint context, ref Guid iid, out IntPtr link);
    [DllImport("ole32.dll")]
    private static extern int PropVariantClear(ref Variant value);
    [DllImport("shell32.dll", CharSet = CharSet.Unicode)]
    private static extern void SHChangeNotify(uint eventId, uint flags, string item, IntPtr other);

    // Native calls keep this helper compatible with trimming.
    private static void** Table(void* instance) => *(void***)instance;
    private static void Release(void* instance)
    {
        if (instance != null) ((delegate* unmanaged[Stdcall]<void*, uint>)Table(instance)[2])(instance);
    }
    private static void* Query(void* instance, Guid iid)
    {
        void* result = null;
        Marshal.ThrowExceptionForHR(((delegate* unmanaged[Stdcall]<void*, Guid*, void**, int>)Table(instance)[0])(instance, &iid, &result));
        return result;
    }

    public static void Repair(Updater updater, string? directory = null)
    {
        var channel = updater.ReadInstallation().Channel;
        var launcher = updater.PathInRoot("SyncBrowser.exe");
        if (!File.Exists(launcher)) return;
        directory ??= Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
            "Microsoft", "Internet Explorer", "Quick Launch", "User Pinned", "TaskBar");
        if (!Directory.Exists(directory)) return;
        var initialized = CoInitializeEx(IntPtr.Zero, 0);
        // An existing STA is also suitable; only release an apartment we acquired.
        if (initialized < 0 && initialized != unchecked((int)0x80010106)) Marshal.ThrowExceptionForHR(initialized);
        try
        {
            foreach (var filename in Directory.EnumerateFiles(directory, "*.lnk"))
                RepairOne(updater, channel, launcher, filename);
        }
        finally { if (initialized >= 0) CoUninitialize(); }
    }

    private static void RepairOne(Updater updater, string channel, string launcher, string filename)
    {
        void* link = null;
        void* persist = null;
        void* store = null;
        try
        {
            var clsid = new Guid("00021401-0000-0000-C000-000000000046");
            var iid = new Guid("000214F9-0000-0000-C000-000000000046");
            CoCreateInstance(ref clsid, IntPtr.Zero, 1, ref iid, out var pointer);
            link = (void*)pointer;
            persist = Query(link, new Guid("0000010B-0000-0000-C000-000000000046"));
            fixed (char* file = filename)
                Marshal.ThrowExceptionForHR(((delegate* unmanaged[Stdcall]<void*, char*, uint, int>)Table(persist)[5])(persist, file, 2));
            char* buffer = stackalloc char[32768];
            buffer[0] = '\0';
            Marshal.ThrowExceptionForHR(((delegate* unmanaged[Stdcall]<void*, char*, int, void*, uint, int>)Table(link)[3])(link, buffer, 32768, null, 0));
            var target = Path.GetFullPath(new string(buffer));
            if (!target.Equals(launcher, StringComparison.OrdinalIgnoreCase) &&
                !(target.StartsWith(updater.PathInRoot("versions") + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase) &&
                  Path.GetFileName(target).ToLowerInvariant() is "brave.exe" or "chrome.exe")) return;
            fixed (char* path = launcher)
                Marshal.ThrowExceptionForHR(((delegate* unmanaged[Stdcall]<void*, char*, int>)Table(link)[20])(link, path));
            fixed (char* root = updater.Root)
                Marshal.ThrowExceptionForHR(((delegate* unmanaged[Stdcall]<void*, char*, int>)Table(link)[9])(link, root));
            store = Query(link, new Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99"));
            var key = new PropertyKey(new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"), 5);
            var value = new Variant { Type = 31, Pointer = Marshal.StringToCoTaskMemUni("Loukious.BraveChromeSync." + channel) };
            try
            {
                Marshal.ThrowExceptionForHR(((delegate* unmanaged[Stdcall]<void*, PropertyKey*, Variant*, int>)Table(store)[6])(store, &key, &value));
            }
            finally { PropVariantClear(ref value); }
            fixed (char* file = filename)
                Marshal.ThrowExceptionForHR(((delegate* unmanaged[Stdcall]<void*, char*, int, int>)Table(persist)[6])(persist, file, 1));
            SHChangeNotify(0x00002000, 0x0005, filename, IntPtr.Zero);
        }
        catch (Exception error) when (error is COMException or IOException or UnauthorizedAccessException or ArgumentException)
        {
            File.WriteAllText(updater.PathInRoot("taskbar-warning.txt"), "Could not refresh a browser taskbar shortcut: " + error.Message);
        }
        finally { Release(store); Release(persist); Release(link); }
    }
}
