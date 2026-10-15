using System.ComponentModel;
using System.Diagnostics;
using System.Diagnostics.Eventing.Reader;
using System.Management;
using System.Runtime.InteropServices;
using System.Reflection.PortableExecutable;
using System.Security.Principal;
using System.Text;
using System.Globalization;
using System.Security.Cryptography;
using Microsoft.Win32;
using Uoo.Core;

namespace Uoo.Platform;

internal sealed record EspPartition(string Volume, string Device, uint DiskNumber, uint PartitionNumber, bool IsSystem, string PartitionGuid);

internal static class WindowsMachine
{
    private const string EspType = "c12a7328-f81f-11d2-ba4b-00a0c93ec93b";
    [DllImport("kernel32.dll", SetLastError = true)] private static extern bool GetFirmwareType(out uint type);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern uint QueryDosDevice(string name, StringBuilder target, int length);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern bool SetVolumeMountPoint(string mount, string volume);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern bool DeleteVolumeMountPoint(string mount);
    [DllImport("kernel32.dll", EntryPoint = "GetVolumeInformationW", CharSet = CharSet.Unicode, ExactSpelling = true, SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool GetVolumeInformation(string rootPath, StringBuilder? volumeName, uint volumeNameSize,
        out uint serialNumber, out uint maximumComponentLength, out uint fileSystemFlags, StringBuilder fileSystemName, uint fileSystemNameSize);

    public static bool Administrator
    {
        get { using var identity = WindowsIdentity.GetCurrent(); return new WindowsPrincipal(identity).IsInRole(WindowsBuiltInRole.Administrator); }
    }
    public static bool Uefi => GetFirmwareType(out var type) && type == 2;
    public static string WindowsDirectory => Environment.GetFolderPath(Environment.SpecialFolder.Windows);
    public static string MachineId => Registry.GetValue(@"HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Cryptography", "MachineGuid", null) as string
        ?? throw new InvalidOperationException("The Windows machine identity could not be read.");

    internal static string BootSession()
    {
        try
        {
            // LastBootUpTime can survive a hibernation/Fast Startup firmware boot. Require
            // hibernation off and bind the proof to the newest physical boot event as well.
            if (Registry.GetValue(@"HKEY_LOCAL_MACHINE\SYSTEM\CurrentControlSet\Control\Power", "HibernateEnabled", null) is not int enabled || enabled != 0 ||
                File.Exists(Path.Combine(Path.GetPathRoot(WindowsDirectory)!, "hiberfil.sys"))) return "";
            using var search = new ManagementObjectSearcher(@"root\CIMV2", "SELECT LastBootUpTime FROM Win32_OperatingSystem");
            using var objects = search.Get();
            List<string> times = [];
            foreach (ManagementObject item in objects)
            using (item)
            {
                if (item["LastBootUpTime"] is not string value || !System.Text.RegularExpressions.Regex.IsMatch(value, @"^[0-9]{14}\.[0-9]{6}[+-][0-9]{3}$")) return "";
                times.Add(ManagementDateTimeConverter.ToDateTime(value).ToUniversalTime().ToString("O", CultureInfo.InvariantCulture));
            }
            if (times.Count != 1) return "";
            var query = new EventLogQuery("System", PathType.LogName,
                "*[System[Provider[@Name='Microsoft-Windows-Kernel-Boot'] and EventID=27]]") { ReverseDirection = true };
            using var reader = new EventLogReader(query);
            using var boot = reader.ReadEvent(TimeSpan.FromSeconds(5));
            if (boot?.RecordId is not long recordId || recordId <= 0 || boot.TimeCreated is not DateTime createdAt) return "";
            var eventIdentity = recordId.ToString(CultureInfo.InvariantCulture) + "\n" + createdAt.ToUniversalTime().ToString("O", CultureInfo.InvariantCulture);
            return Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(MachineId + "\n" + times[0] + "\n" + eventIdentity)));
        }
        catch (Exception error) when (error is ManagementException or EventLogException or UnauthorizedAccessException or System.Runtime.InteropServices.COMException or ArgumentException or IOException or InvalidOperationException or System.Security.SecurityException)
        { return ""; }
    }

    public static string NativeVolume(string volume)
    {
        var name = volume.TrimEnd('\\');
        if (name.StartsWith(@"\\?\", StringComparison.Ordinal)) name = name[4..];
        var buffer = new StringBuilder(32768);
        if (QueryDosDevice(name, buffer, buffer.Capacity) == 0) throw new Win32Exception(Marshal.GetLastWin32Error());
        return buffer.ToString().Split('\0')[0];
    }

    public static List<EspPartition> Partitions()
    {
        List<EspPartition> result = [];
        using var diskSearch = new ManagementObjectSearcher(@"root\Microsoft\Windows\Storage", "SELECT * FROM MSFT_Disk");
        using var disks = diskSearch.Get();
        var safeDisks = disks.Cast<ManagementObject>().Where(d => !Convert.ToBoolean(d["IsOffline"]) &&
            !Convert.ToBoolean(d["IsReadOnly"]) && Convert.ToUInt32(d["PartitionStyle"]) == 2 &&
            Convert.ToUInt16(d["BusType"]) is not (0 or 4 or 7 or 15 or 16)).Select(d => Convert.ToUInt32(d["Number"])).ToHashSet();
        using var search = new ManagementObjectSearcher(@"root\Microsoft\Windows\Storage", "SELECT * FROM MSFT_Partition");
        using var objects = search.Get();
        foreach (ManagementObject item in objects)
        using (item)
        {
            if (!Guid.TryParse(item["GptType"] as string, out var type) || type.ToString() != EspType ||
                !safeDisks.Contains(Convert.ToUInt32(item["DiskNumber"])) || Convert.ToBoolean(item["IsReadOnly"]) ||
                Convert.ToBoolean(item["IsOffline"])) continue;
            var paths = item["AccessPaths"] as string[] ?? [];
            var volume = paths.FirstOrDefault(p => System.Text.RegularExpressions.Regex.IsMatch(p,
                @"^\\\\\?\\Volume\{[0-9a-fA-F-]{36}\}\\$"));
            if (volume is null) continue;
            var fileSystem = new StringBuilder(256);
            if (!GetVolumeInformation(volume, null, 0, out _, out _, out var flags, fileSystem, (uint)fileSystem.Capacity) ||
                (flags & 0x00080000) != 0 || !fileSystem.ToString().Equals("FAT32", StringComparison.OrdinalIgnoreCase)) continue;
            result.Add(new(volume, NativeVolume(volume), Convert.ToUInt32(item["DiskNumber"]),
                Convert.ToUInt32(item["PartitionNumber"]), Convert.ToBoolean(item["IsSystem"]), Guid.Parse((string)item["Guid"]).ToString("B")));
        }
        return result;
    }

    public static EspPartition ValidateEsp(string path, bool system = false)
    {
        if (string.IsNullOrWhiteSpace(path)) throw new ArgumentException("Select an existing local GPT EFI system partition.");
        var candidates = Partitions().Where(p => p.Volume.Equals(path.TrimEnd('\\') + "\\", StringComparison.OrdinalIgnoreCase) ||
            p.Device.Equals(path.TrimEnd('\\'), StringComparison.OrdinalIgnoreCase)).ToList();
        // A drive root is accepted only if it resolves to the exact validated ESP volume.
        if (candidates.Count == 0 && System.Text.RegularExpressions.Regex.IsMatch(path, @"^[A-Za-z]:\\?$"))
        {
            var device = NativeVolume(path[..2]);
            candidates = Partitions().Where(p => p.Device.Equals(device, StringComparison.OrdinalIgnoreCase)).ToList();
        }
        if (candidates.Count != 1 || (system && !candidates[0].IsSystem))
            throw new ArgumentException("The selected path is not the verified writable local GPT system ESP.");
        return candidates[0];
    }

    internal static void ValidateWindowsLoader(BcdRecord entry)
    {
        if (entry.Type != Bcd.WindowsLoader) return;
        const uint osDevice = 0x21000001;
        var application = entry.Device;
        var operatingSystem = entry.Elements.GetValueOrDefault(osDevice) as BcdDevice;
        if (application is not { Type: 2, AdditionalOptions.Length: 0 } || operatingSystem is not { Type: 2, AdditionalOptions.Length: 0 } ||
            application.Path.Length == 0 || operatingSystem.Path.Length == 0)
            throw new ArgumentException("The selected Windows loader's application or OS partition is unresolved or unsupported. Choose an available local Windows installation.");
        var loaderPath = RelativeWindowsBootPath(entry.Text(Bcd.Path));
        var systemRoot = RelativeWindowsBootPath(entry.Text(Bcd.SystemRoot));
        List<(string Volume, string Device)> volumes = [];
        using var search = new ManagementObjectSearcher(@"root\CIMV2", "SELECT DeviceID, DriveType FROM Win32_Volume");
        using var objects = search.Get();
        foreach (ManagementObject item in objects)
        using (item)
        {
            if (Convert.ToUInt32(item["DriveType"]) is not (2 or 3) || item["DeviceID"] is not string volume ||
                !System.Text.RegularExpressions.Regex.IsMatch(volume, @"^\\\\\?\\Volume\{[0-9a-fA-F-]{36}\}\\$")) continue;
            var fileSystem = new StringBuilder(256);
            if (!GetVolumeInformation(volume, null, 0, out _, out _, out _, fileSystem, (uint)fileSystem.Capacity)) continue;
            try { volumes.Add((volume, NativeVolume(volume))); }
            catch (Win32Exception) { /* An unavailable candidate must not resolve a boot target. */ }
        }
        string Resolve(string device)
        {
            var found = volumes.Where(v => v.Device.Equals(device, StringComparison.OrdinalIgnoreCase)).Select(v => v.Volume)
                .Distinct(StringComparer.OrdinalIgnoreCase).ToList();
            return found.Count == 1 ? found[0] : throw new ArgumentException("The selected Windows loader references a missing, offline, or ambiguous local partition.");
        }
        var loader = Path.Combine(Resolve(application.Path), loaderPath);
        var kernel = Path.Combine(Resolve(operatingSystem.Path), systemRoot, "System32", "ntoskrnl.exe");
        foreach (var path in new[] { loader, kernel })
        {
            SafePath(path);
            if (!File.Exists(path)) throw new ArgumentException("The selected Windows installation's boot loader or kernel is missing. Its boot target cannot be selected or restored.");
        }
        try
        {
            using var file = File.OpenRead(loader); using var pe = new PEReader(file);
            var headers = pe.PEHeaders;
            var machine = RuntimeInformation.OSArchitecture == Architecture.Arm64 ? Machine.Arm64 : Machine.Amd64;
            if (headers.PEHeader is null || headers.PEHeader.Magic != PEMagic.PE32Plus || headers.PEHeader.Subsystem != (Subsystem)16 ||
                headers.CoffHeader.Machine != machine || !headers.CoffHeader.Characteristics.HasFlag(Characteristics.ExecutableImage))
                throw new ArgumentException("The selected Windows loader is not a native 64-bit Windows boot application.");
        }
        catch (BadImageFormatException error) { throw new ArgumentException("The selected Windows boot application has an invalid PE image.", error); }
    }

    private static string RelativeWindowsBootPath(string value)
    {
        if (string.IsNullOrWhiteSpace(value) || value.Length > 1024 || value[0] != '\\' || value.StartsWith(@"\\", StringComparison.Ordinal) ||
            value.Any(c => char.IsControl(c) || "/:*?\"<>|".Contains(c)))
            throw new ArgumentException("The selected Windows entry has an invalid loader path or system root.");
        var relative = value[1..].TrimEnd('\\');
        if (relative.Split('\\').Any(part => part.Length == 0 || part is "." or ".." || part.EndsWith('.') || part.EndsWith(' ')))
            throw new ArgumentException("The selected Windows entry has an invalid loader path or system root.");
        return relative;
    }

    public static string EfiPath(EspPartition partition, string loader)
    {
        BootValidation.ValidateEfiLoaderPath(loader);
        var full = Path.Combine(partition.Volume, loader.TrimStart('\\'));
        SafePath(full);
        var info = new FileInfo(full);
        if (!info.Exists || info.Length is < 64 or > 64 * 1024 * 1024) throw new ArgumentException("EFI application is missing or too large.");
        using var file = info.OpenRead();
        if (file.ReadByte() != 'M' || file.ReadByte() != 'Z') throw new ArgumentException("EFI application is not a PE image.");
        file.Position = 0;
        ValidateEfiImage(file, RuntimeInformation.OSArchitecture);
        return full;
    }

    internal static void ValidateEfiImage(Stream stream, Architecture architecture)
    {
        try
        {
            using var pe = new PEReader(stream, PEStreamOptions.LeaveOpen);
            var header = pe.PEHeaders;
            if (header.PEHeader is null || header.PEHeader.Magic != PEMagic.PE32Plus || header.PEHeader.Subsystem != Subsystem.EfiApplication ||
                !header.CoffHeader.Characteristics.HasFlag(Characteristics.ExecutableImage) || header.CoffHeader.Characteristics.HasFlag(Characteristics.Dll) ||
                header.CoffHeader.NumberOfSections is < 1 or > 96)
                throw new ArgumentException("The selected PE image is not a 64-bit executable EFI boot application.");
            if (architecture is not (Architecture.X64 or Architecture.Arm64) ||
                architecture == Architecture.X64 && header.CoffHeader.Machine != Machine.Amd64 ||
                architecture == Architecture.Arm64 && header.CoffHeader.Machine != Machine.Arm64)
                throw new ArgumentException("EFI application does not match this Windows machine's architecture.");
            if (header.PEHeader.SizeOfHeaders <= 0 || header.PEHeader.SizeOfHeaders > stream.Length ||
                header.PEHeader.SizeOfImage <= 0 || header.SectionHeaders.Any(section =>
                    section.SizeOfRawData < 0 || section.PointerToRawData < 0 ||
                    section.SizeOfRawData > 0 && (section.PointerToRawData < header.PEHeader.SizeOfHeaders ||
                        (long)section.PointerToRawData + section.SizeOfRawData > stream.Length)))
                throw new ArgumentException("EFI application contains truncated or invalid section data.");
        }
        catch (BadImageFormatException error) { throw new ArgumentException("EFI application has an invalid or truncated PE image.", error); }
    }

    public static void SafePath(string path)
    {
        var current = new FileInfo(path).FullName;
        while (!string.IsNullOrEmpty(current) && current != Path.GetPathRoot(current))
        {
            if ((File.Exists(current) || Directory.Exists(current)) && (File.GetAttributes(current) & FileAttributes.ReparsePoint) != 0)
                throw new IOException("Reparse points are not supported in boot or backup paths.");
            current = Path.GetDirectoryName(current) ?? "";
        }
    }

    public static (uint? Protection, string Detail) BitLocker()
    {
        try
        {
            var root = Path.GetPathRoot(WindowsDirectory)!.TrimEnd('\\');
            using var scope = new ManagementObjectSearcher(new ManagementScope(@"\\.\root\CIMV2\Security\MicrosoftVolumeEncryption",
                new ConnectionOptions { Authentication = AuthenticationLevel.PacketPrivacy, EnablePrivileges = true }),
                new ObjectQuery("SELECT * FROM Win32_EncryptableVolume"));
            using var objects = scope.Get();
            foreach (ManagementObject item in objects)
            using (item)
                if (string.Equals(item["DriveLetter"] as string, root, StringComparison.OrdinalIgnoreCase))
                {
                    using var value = item.InvokeMethod("GetProtectionStatus", null, null);
                    if (value is null || Convert.ToUInt32(value["ReturnValue"]) != 0) break;
                    var status = Convert.ToUInt32(value["ProtectionStatus"]);
                    return (status, status == 1 ? "Protection is on. Boot changes can require the recovery key; Uoo never suspends protection." :
                        status == 0 ? "Protection is off. Uoo does not change encryption or protectors." : "Protection status is unknown.");
                }
            return (null, "No readable BitLocker status for the running Windows volume.");
        }
        catch (Exception error) when (error is ManagementException or UnauthorizedAccessException or System.Runtime.InteropServices.COMException)
        { return (null, "BitLocker status could not be read: " + error.Message); }
    }

    public static List<DiagnosticItem> Diagnostics()
    {
        var bitLocker = BitLocker();
        var secure = Registry.GetValue(@"HKEY_LOCAL_MACHINE\SYSTEM\CurrentControlSet\Control\SecureBoot\State", "UEFISecureBootEnabled", null);
        return [new("Boot mode", Uefi ? "OK" : "Info", Uefi ? "UEFI" : "Legacy BIOS; firmware registration and UEFI repair are unavailable."),
            new("Secure Boot", secure is int ? "OK" : "Unknown", secure is int value ? value == 1 ? "Enabled" : "Disabled" : "Unavailable"),
            new("BitLocker", bitLocker.Protection == 0 ? "OK" : "Attention", bitLocker.Detail),
            new("Administrator", Administrator ? "OK" : "Info", Administrator ? "Elevated" : "Changes require Windows administrator approval.")];
    }

    public static RepairPlan RepairPlan()
    {
        try
        {
            if (!Uefi) throw new InvalidOperationException("Repair supports the running Windows installation on a local GPT/UEFI system.");
            var esp = Partitions().Where(p => p.IsSystem).ToList();
            if (esp.Count != 1) throw new InvalidOperationException("Exactly one verified writable local system ESP is required.");
            foreach (var relative in new[] { @"System32\winload.efi", @"Boot\EFI\bootmgfw.efi", @"System32\Config\BCD-Template" })
            {
                var full = Path.Combine(WindowsDirectory, relative); SafePath(full);
                if (!File.Exists(full)) throw new InvalidOperationException("The running Windows boot source is incomplete: " + relative);
            }
            return new() { Available = true, WindowsDirectory = WindowsDirectory, SystemPartition = esp[0].Volume,
                Description = "Refresh the running Windows boot files and BCD on the verified system ESP, preserving menu settings and firmware order.", Confirmation = "REPAIR" };
        }
        catch (Exception error) when (error is ManagementException or IOException or UnauthorizedAccessException or InvalidOperationException or System.Runtime.InteropServices.COMException)
        { return new() { Available = false, Reason = error.Message }; }
    }

    public static void RunBcdBoot(EspPartition esp, string current)
    {
        var letter = Enumerable.Range('D', 'Z' - 'D' + 1).Reverse().Select(c => (char)c + ":\\")
            .FirstOrDefault(p => !DriveInfo.GetDrives().Any(d => d.Name.Equals(p, StringComparison.OrdinalIgnoreCase)));
        if (letter is null) throw new IOException("A temporary free drive letter is required for BCDBoot.");
        if (!SetVolumeMountPoint(letter, esp.Volume)) throw new Win32Exception(Marshal.GetLastWin32Error());
        try
        {
            var executable = Path.Combine(Environment.SystemDirectory, "bcdboot.exe");
            var info = new ProcessStartInfo(executable) { UseShellExecute = false, CreateNoWindow = true, RedirectStandardOutput = true, RedirectStandardError = true };
            foreach (var argument in new[] { WindowsDirectory, "/s", letter[..2], "/f", "UEFI", "/d", "/m", Bcd.Id(current) }) info.ArgumentList.Add(argument);
            using var process = Process.Start(info) ?? throw new IOException("BCDBoot did not start.");
            var stdout = process.StandardOutput.ReadToEndAsync(); var stderr = process.StandardError.ReadToEndAsync();
            if (!process.WaitForExit(120000)) { process.Kill(true); process.WaitForExit(); throw new IOException("BCDBoot timed out; boot files will be restored."); }
            Task.WaitAll(stdout, stderr);
            if (process.ExitCode != 0) throw new IOException($"BCDBoot failed ({process.ExitCode}): {stderr.Result} {stdout.Result}");
        }
        finally { if (!DeleteVolumeMountPoint(letter)) throw new Win32Exception(Marshal.GetLastWin32Error(), "Temporary ESP mount could not be removed."); }
    }
}
