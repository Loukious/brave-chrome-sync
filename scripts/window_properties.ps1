param([int[]]$ProcessIds)
$ErrorActionPreference = 'Stop'
Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
namespace SyncWindowProperties {
  [StructLayout(LayoutKind.Sequential)] public struct PropertyKey {
    public Guid Format; public uint Id;
    public PropertyKey(uint id) { Format = new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"); Id = id; }
  }
  [StructLayout(LayoutKind.Explicit, Size=24)] public struct Variant {
    [FieldOffset(0)] public ushort Type;
    [FieldOffset(8)] public IntPtr Pointer;
  }
  [ComImport, Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
  public interface Store {
    [PreserveSig] int GetCount(out uint count);
    [PreserveSig] int GetAt(uint index, out PropertyKey key);
    [PreserveSig] int GetValue(ref PropertyKey key, out Variant value);
    [PreserveSig] int SetValue(ref PropertyKey key, ref Variant value);
    [PreserveSig] int Commit();
  }
  public sealed class Window {
    public long Handle; public uint ProcessId; public string AppId; public string Relaunch; public bool Visible;
    public string[] RelaunchArguments;
  }
  public static class Reader {
    delegate bool Callback(IntPtr window, IntPtr data);
    [DllImport("user32.dll")] static extern bool EnumWindows(Callback callback, IntPtr data);
    [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr window, out uint pid);
    [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr window);
    [DllImport("shell32.dll")] static extern int SHGetPropertyStoreForWindow(IntPtr window, ref Guid iid, out Store store);
    [DllImport("ole32.dll")] static extern int PropVariantClear(ref Variant value);
    [DllImport("shell32.dll", CharSet=CharSet.Unicode)] static extern IntPtr CommandLineToArgvW(string command, out int count);
    [DllImport("kernel32.dll")] static extern IntPtr LocalFree(IntPtr pointer);
    static string[] Arguments(string command) {
      if (command == "") return new string[0];
      int count; var pointer = CommandLineToArgvW(command, out count);
      if (pointer == IntPtr.Zero) return new string[0];
      try {
        var result = new string[count];
        for (int i=0; i<count; i++) result[i] = Marshal.PtrToStringUni(Marshal.ReadIntPtr(pointer, i * IntPtr.Size)) ?? "";
        return result;
      } finally { LocalFree(pointer); }
    }
    static string Read(Store store, uint id) {
      var key = new PropertyKey(id); Variant value;
      if (store.GetValue(ref key, out value) != 0) return "";
      try { return value.Type == 31 ? Marshal.PtrToStringUni(value.Pointer) ?? "" : ""; }
      finally { PropVariantClear(ref value); }
    }
    public static Window[] ForProcesses(int[] ids) {
      var wanted = new HashSet<int>(ids); var found = new List<Window>();
      EnumWindows((window, _) => {
        uint pid; GetWindowThreadProcessId(window, out pid);
        if (!wanted.Contains((int)pid)) return true;
        var iid = typeof(Store).GUID; Store store;
        if (SHGetPropertyStoreForWindow(window, ref iid, out store) != 0) return true;
        try {
          var appId = Read(store, 5); var relaunch = Read(store, 2);
          if (appId != "" || relaunch != "") found.Add(new Window {
            Handle = window.ToInt64(), ProcessId = pid, AppId = appId,
            Relaunch = relaunch, RelaunchArguments = Arguments(relaunch), Visible = IsWindowVisible(window)});
        } finally { Marshal.ReleaseComObject(store); }
        return true;
      }, IntPtr.Zero);
      return found.ToArray();
    }
  }
}
'@
[SyncWindowProperties.Reader]::ForProcesses($ProcessIds) | ConvertTo-Json -Depth 4
