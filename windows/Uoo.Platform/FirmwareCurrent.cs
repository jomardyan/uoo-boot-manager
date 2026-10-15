using System.Buffers.Binary;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Text;
using Microsoft.Win32.SafeHandles;

namespace Uoo.Platform;

internal sealed class TokenPrivilege : IDisposable
{
    [StructLayout(LayoutKind.Sequential)] private struct Luid { public uint Low; public int High; }
    [StructLayout(LayoutKind.Sequential)] private struct Privileges { public uint Count; public Luid Luid; public uint Attributes; }
    [DllImport("kernel32.dll")] private static extern IntPtr GetCurrentProcess();
    [DllImport("advapi32.dll", SetLastError = true)] private static extern bool OpenProcessToken(IntPtr process, uint access, out SafeAccessTokenHandle token);
    [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)] private static extern bool LookupPrivilegeValue(string? system, string name, out Luid luid);
    [DllImport("advapi32.dll", SetLastError = true)] private static extern bool AdjustTokenPrivileges(SafeAccessTokenHandle token, bool disable, ref Privileges state, uint length, out Privileges previous, out uint required);
    private readonly SafeAccessTokenHandle token;
    private Privileges previous;
    public TokenPrivilege(string name)
    {
        if (!OpenProcessToken(GetCurrentProcess(), 0x28, out token)) throw new Win32Exception(Marshal.GetLastWin32Error());
        try
        {
            if (!LookupPrivilegeValue(null, name, out var luid)) throw new Win32Exception(Marshal.GetLastWin32Error());
            var state = new Privileges { Count = 1, Luid = luid, Attributes = 2 };
            Marshal.SetLastPInvokeError(0);
            if (!AdjustTokenPrivileges(token, false, ref state, (uint)Marshal.SizeOf<Privileges>(), out previous, out _) || Marshal.GetLastWin32Error() != 0)
                throw new Win32Exception(Marshal.GetLastWin32Error());
        }
        catch { token.Dispose(); throw; }
    }
    public void Dispose()
    {
        try { _ = AdjustTokenPrivileges(token, false, ref previous, 0, out _, out _); }
        finally { token.Dispose(); }
    }
}

internal sealed record FirmwareBootIdentity(string PartitionGuid, string LoaderPath, string Description);
internal static class FirmwareCurrent
{
    private const string Global = "{8be4df61-93ca-11d2-aa0d-00e098032b8c}";
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern uint GetFirmwareEnvironmentVariableEx(string name, string guid, byte[] value, uint size, out uint attributes);
    public static FirmwareBootIdentity Read()
    {
        using var privilege = new TokenPrivilege("SeSystemEnvironmentPrivilege");
        var current = ReadVariable("BootCurrent");
        if (current.Length != 2) throw new IOException("Firmware BootCurrent has an invalid size.");
        var number = BinaryPrimitives.ReadUInt16LittleEndian(current);
        return Parse(ReadVariable("Boot" + number.ToString("X4")));
    }
    private static byte[] ReadVariable(string name)
    {
        byte[] value = new byte[64 * 1024];
        var size = GetFirmwareEnvironmentVariableEx(name, Global, value, (uint)value.Length, out _);
        if (size == 0) throw new Win32Exception(Marshal.GetLastWin32Error(), "Current firmware boot entry could not be verified.");
        return value.AsSpan(0, checked((int)size)).ToArray();
    }
    internal static FirmwareBootIdentity Parse(byte[] data)
    {
        if (data.Length is < 12 or > 64 * 1024) throw new IOException("Invalid EFI load option size.");
        var pathLength = BinaryPrimitives.ReadUInt16LittleEndian(data.AsSpan(4));
        int position = 6;
        while (position + 1 < data.Length && (data[position] != 0 || data[position + 1] != 0)) position += 2;
        if (position + 1 >= data.Length) throw new IOException("EFI load option description is unterminated.");
        var description = new UnicodeEncoding(false, false, true).GetString(data, 6, position - 6);
        position += 2;
        var end = checked(position + pathLength);
        if (position > data.Length || pathLength < 4 || end > data.Length) throw new IOException("Invalid EFI device path size.");
        string partition = ""; StringBuilder loader = new(); bool terminated = false;
        while (position < end)
        {
            if (position + 4 > end) throw new IOException("Truncated EFI device node.");
            var length = BinaryPrimitives.ReadUInt16LittleEndian(data.AsSpan(position + 2));
            if (length < 4 || position + length > end) throw new IOException("Invalid EFI device node length.");
            var type = data[position]; var subtype = data[position + 1];
            if (type == 4 && subtype == 1)
            {
                if (length != 42 || data[position + 40] != 2 || data[position + 41] != 2 || partition.Length != 0)
                    throw new IOException("Current EFI entry is not an unambiguous GPT partition path.");
                partition = new Guid(data.AsSpan(position + 24, 16)).ToString("B");
            }
            if (type == 4 && subtype == 4)
            {
                if ((length - 4) % 2 != 0 || length < 6 || data[position + length - 2] != 0 || data[position + length - 1] != 0)
                    throw new IOException("Invalid EFI file path node.");
                var part = new UnicodeEncoding(false, false, true).GetString(data, position + 4, length - 6);
                if (part.Contains('\0')) throw new IOException("EFI file path contains an embedded terminator.");
                loader.Append(part);
            }
            if (type == 0x7f)
            {
                if (subtype != 0xff || length != 4 || position + length != end) throw new IOException("Multiple EFI device path instances are unsupported.");
                terminated = true;
            }
            position += length;
        }
        if (!terminated || partition.Length == 0 || loader.Length == 0) throw new IOException("The current firmware partition and EFI loader could not be identified.");
        return new(partition, loader.ToString(), description);
    }
}
