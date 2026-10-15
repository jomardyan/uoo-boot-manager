using System.Management;
using System.Collections;
using System.Text.Json;
using System.Diagnostics;
using Uoo.Core;
using Microsoft.Win32;

namespace Uoo.Platform;

// These are the public BCD WMI element identifiers, not localized BCDEdit names.
internal static class Bcd
{
    public const string Manager = "{9dea862c-5cdd-4e70-acc1-f32b344d4795}";
    public const string Firmware = "{a5a30fa2-3d06-4e9f-b5f4-a01df9d1fcba}";
    public const string Current = "{fa926493-6f1c-4193-a414-58f0b2456d1e}";
    public const uint Device = 0x11000001, Path = 0x12000002, Description = 0x12000004;
    public const uint SystemRoot = 0x22000002, Order = 0x24000001, Sequence = 0x24000002;
    public const uint Default = 0x23000003, Timeout = 0x25000004, DisplayMenu = 0x26000020;
    public const uint SafeBoot = 0x25000080, BootLog = 0x26000090, Sos = 0x26000091;
    public const uint BaseVideo = 0x26000040, Recovery = 0x16000009, MenuPolicy = 0x250000C2;
    public const uint WindowsLoader = 0x10200003, FirmwareApplication = 0x101fffff;
    public static string Id(string value) => Guid.TryParse(value, out var id) && id != Guid.Empty
        ? id.ToString("B") : throw new ArgumentException("A full nonempty BCD object GUID is required.");
}

internal sealed record BcdDevice(uint Type, string Path, string AdditionalOptions);
internal sealed record BcdRecord(string Id, uint Type, Dictionary<uint, object?> Elements, Dictionary<uint, string>? RawElements = null)
{
    public string Text(uint type) => Elements.GetValueOrDefault(type) as string ?? "";
    public ulong Number(uint type, ulong fallback = 0) => Elements.TryGetValue(type, out var value) ? Convert.ToUInt64(value) : fallback;
    public bool Flag(uint type, bool fallback = false) => Elements.TryGetValue(type, out var value) ? Convert.ToBoolean(value) : fallback;
    public List<string> Ids(uint type) => (Elements.GetValueOrDefault(type) as string[] ?? []).Select(Bcd.Id).ToList();
    public BcdDevice? Device => Elements.GetValueOrDefault(Bcd.Device) as BcdDevice;
}

internal sealed class BcdStore : IDisposable
{
    private ManagementScope scope = null!;
    private ManagementClass storeClass = null!;
    private ManagementObject store = null!;
    private readonly string file;
    private readonly Action? cleanup;
    internal bool IsOffline => file.Length != 0;

    public BcdStore(string filePath = "", Action? onDispose = null)
    {
        file = filePath;
        cleanup = onDispose;
        Refresh();
    }

    private void Refresh()
    {
        store?.Dispose(); storeClass?.Dispose();
        scope = new ManagementScope(@"\\.\root\WMI", new ConnectionOptions
        { EnablePrivileges = true, Impersonation = ImpersonationLevel.Impersonate, Authentication = AuthenticationLevel.PacketPrivacy });
        scope.Connect();
        storeClass = new ManagementClass(scope, new ManagementPath("BcdStore"), null);
        using var input = storeClass.GetMethodParameters("OpenStore");
        input["File"] = file;
        using var result = storeClass.InvokeMethod("OpenStore", input, null);
        Check(result, "OpenStore");
        store = new ManagementObject(scope, new ManagementPath($"BcdStore.FilePath=\"{Escape(file)}\""), null);
    }

    public static BcdStore CreateOffline(string path)
    {
        if (string.IsNullOrEmpty(path) || !System.IO.Path.IsPathFullyQualified(path) || File.Exists(path))
            throw new ArgumentException("A new absolute offline BCD file path is required.");
        var scope = new ManagementScope(@"\\.\root\WMI", new ConnectionOptions
        { EnablePrivileges = true, Impersonation = ImpersonationLevel.Impersonate, Authentication = AuthenticationLevel.PacketPrivacy });
        scope.Connect();
        using var cls = new ManagementClass(scope, new ManagementPath("BcdStore"), null);
        using var input = cls.GetMethodParameters("CreateStore"); input["File"] = path;
        using var result = cls.InvokeMethod("CreateStore", input, null); Check(result, "CreateStore");
        return new BcdStore(path);
    }

    private static string Escape(string text) => text.Replace("\\", "\\\\", StringComparison.Ordinal).Replace("\"", "\\\"", StringComparison.Ordinal);
    private static void Check(ManagementBaseObject? result, string operation)
    {
        if (result is null || !Convert.ToBoolean(result["ReturnValue"]))
            throw new InvalidOperationException($"Windows BCD provider rejected {operation}. No localized command output was parsed.");
    }

    private ManagementBaseObject Invoke(ManagementObject target, string method, params (string Name, object Value)[] parameters)
    {
        using var input = target.GetMethodParameters(method);
        foreach (var (name, value) in parameters) input[name] = value;
        var result = target.InvokeMethod(method, input, null);
        try { Check(result, method); return result!; }
        catch { result?.Dispose(); throw; }
    }

    private ManagementObject Open(string id)
    {
        using var result = Invoke(store, "OpenObject", ("Id", Bcd.Id(id)));
        var embedded = (ManagementBaseObject)result["Object"];
        // Resolve the documented current-object GUID to the real object's Id.
        var actual = Bcd.Id((string)embedded["Id"]);
        return new ManagementObject(scope,
            new ManagementPath($"BcdObject.Id=\"{actual}\",StoreFilePath=\"{Escape(file)}\""), null);
    }

    public BcdRecord Read(string id)
    {
        using var obj = Open(id);
        obj.Get();
        using var result = Invoke(obj, "EnumerateElements");
        Dictionary<uint, object?> elements = [];
        Dictionary<uint, string> raw = [];
        foreach (var element in (ManagementBaseObject[]?)result["Elements"] ?? [])
        {
            using (element)
            {
                var type = Convert.ToUInt32(element["Type"]);
                raw[type] = JsonSerializer.Serialize(Canonical(element));
                var format = (type >> 24) & 0x0f;
                object? value = format switch
                {
                    1 => DeviceValue(element["Device"] as ManagementBaseObject),
                    2 => element["String"] as string ?? "",
                    3 => element["Id"] as string ?? "",
                    4 => element["Ids"] as string[] ?? [],
                    5 => Convert.ToUInt64(element["Integer"]),
                    6 => Convert.ToBoolean(element["Boolean"]),
                    7 => element["Integers"] as ulong[] ?? [],
                    _ => null
                };
                elements[type] = value;
            }
        }
        return new BcdRecord(Bcd.Id((string)obj["Id"]), Convert.ToUInt32(obj["Type"]), elements, raw);
    }

    private static BcdDevice? DeviceValue(ManagementBaseObject? device)
    {
        if (device is null) return null;
        string Property(string name) => device.Properties.Cast<PropertyData>().FirstOrDefault(p => p.Name == name)?.Value as string ?? "";
        var additional = Property("AdditionalOptions");
        if (Guid.TryParse(additional, out var optionId) && optionId == Guid.Empty) additional = "";
        return new(Convert.ToUInt32(device["DeviceType"]), Property("Path"), additional);
    }

    private static object? Canonical(object? value)
    {
        if (value is ManagementBaseObject obj)
            return obj.Properties.Cast<PropertyData>().Where(p => p.Name != "StoreFilePath")
                .OrderBy(p => p.Name, StringComparer.Ordinal).ToDictionary(p => p.Name, p => Canonical(p.Value));
        if (value is IEnumerable array && value is not string) return array.Cast<object?>().Select(Canonical).ToArray();
        return value;
    }

    public List<BcdRecord> Enumerate()
    {
        using var result = Invoke(store, "EnumerateObjects", ("Type", 0u));
        List<BcdRecord> records = [];
        foreach (var item in (ManagementBaseObject[]?)result["Objects"] ?? [])
            using (item) records.Add(Read((string)item["Id"]));
        return records;
    }

    public void String(string id, uint type, string value) => Set(id, "SetStringElement", ("Type", type), ("String", value));
    public void Integer(string id, uint type, ulong value) => Set(id, "SetIntegerElement", ("Type", type), ("Integer", value));
    public void Boolean(string id, uint type, bool value) => Set(id, "SetBooleanElement", ("Type", type), ("Boolean", value));
    public void Object(string id, uint type, string value) => Set(id, "SetObjectElement", ("Type", type), ("Id", Bcd.Id(value)));
    public void Objects(string id, uint type, IEnumerable<string> values)
    {
        var ids = values.Select(Bcd.Id).ToArray();
        if (ids.Length == 0) DeleteElement(id, type);
        else Set(id, "SetObjectListElement", ("Type", type), ("Ids", ids));
    }
    public void Partition(string id, string path) => Set(id, "SetPartitionDeviceElement", ("Type", Bcd.Device),
        ("DeviceType", 2u), ("AdditionalOptions", ""), ("Path", path));
    public void DeleteElement(string id, uint type)
    {
        if (Read(id).Elements.ContainsKey(type)) Set(id, "DeleteElement", ("Type", type));
    }
    private void Set(string id, string method, params (string Name, object Value)[] parameters)
    { using var obj = Open(id); using var result = Invoke(obj, method, parameters); }
    public void Delete(string id) { using var result = Invoke(store, "DeleteObject", ("Id", Bcd.Id(id))); }
    public string Create(string id, uint type)
    {
        using var result = Invoke(store, "CreateObject", ("Id", Bcd.Id(id)), ("Type", type));
        return Bcd.Id((string)((ManagementBaseObject)result["Object"])["Id"]);
    }
    public string Copy(string id)
    {
        // The Windows 11 WMI provider rejects same-store CopyObject on otherwise valid offline stores.
        // BCDEdit performs the native, complete copy, including opaque device/extension elements.
        // Its localized output is never parsed: WMI supplies the before/after GUIDs and all values.
        var source = Read(id);
        var before = Enumerate().Select(record => record.Id).ToHashSet(StringComparer.OrdinalIgnoreCase);
        var description = source.Text(Bcd.Description);
        _ = BootValidation.Description(description);
        var start = new ProcessStartInfo(System.IO.Path.Combine(Environment.SystemDirectory, "bcdedit.exe"))
        { UseShellExecute = false, CreateNoWindow = true, RedirectStandardOutput = true, RedirectStandardError = true };
        if (file.Length != 0) { start.ArgumentList.Add("/store"); start.ArgumentList.Add(file); }
        foreach (var argument in new[] { "/copy", source.Id, "/d", description }) start.ArgumentList.Add(argument);
        using var process = Process.Start(start) ?? throw new IOException("Windows BCDEdit could not start the boot-entry copy.");
        var output = process.StandardOutput.ReadToEndAsync(); var error = process.StandardError.ReadToEndAsync();
        if (!process.WaitForExit(60000))
        { process.Kill(true); process.WaitForExit(); throw new IOException("Windows boot-entry copy timed out."); }
        Task.WaitAll(output, error);
        if (process.ExitCode != 0) throw new IOException($"Windows boot-entry copy failed ({process.ExitCode}): {error.Result} {output.Result}");
        var added = Enumerate().Where(record => !before.Contains(record.Id)).ToList();
        if (added.Count != 1 || added[0].Type != source.Type || !SamePayload(source, Read(source.Id)) || !SamePayload(source, added[0]))
            throw new IOException("Windows boot-entry copy did not preserve the source and all copied element values.");
        return added[0].Id;
    }
    private static bool SamePayload(BcdRecord first, BcdRecord second)
    {
        if (first.RawElements is null || second.RawElements is null || first.RawElements.Count != second.RawElements.Count) return false;
        static string WithoutOwner(string json)
        {
            using var value = JsonDocument.Parse(json);
            return JsonSerializer.Serialize(value.RootElement.EnumerateObject().Where(property => !property.Name.Equals("ObjectId", StringComparison.OrdinalIgnoreCase))
                .OrderBy(property => property.Name, StringComparer.Ordinal).ToDictionary(property => property.Name, property => property.Value));
        }
        return first.Type == second.Type && first.RawElements.All(element => second.RawElements.TryGetValue(element.Key, out var copied) &&
            WithoutOwner(element.Value) == WithoutOwner(copied));
    }
    public void Export(string path) { using var result = Invoke(store, "ExportStore", ("File", path)); }
    public void RestoreCoreFrom(BcdStore source)
    {
        if (!source.IsOffline || !System.IO.Path.IsPathFullyQualified(source.file) ||
            file.Length != 0 && System.IO.Path.GetFullPath(file).Equals(System.IO.Path.GetFullPath(source.file), StringComparison.OrdinalIgnoreCase))
            throw new InvalidOperationException("BCD restoration requires a distinct, absolute offline source store.");
        // Global store import rewrites firmware GUID mappings even when /clean is not
        // requested. Copy only concrete Windows objects across stores; leave the live
        // firmware namespace and every vendor registration in place.
        static bool Firmware(BcdRecord record) => record.Id != Bcd.Manager &&
            (record.Id == Bcd.Firmware || (record.Type & 0xfff00000) == 0x10100000);
        var archived = source.Enumerate(); var before = Enumerate();
        var expected = archived.Where(record => !Firmware(record)).ToList();
        var manager = expected.SingleOrDefault(record => record.Id == Bcd.Manager) ?? throw new IOException("The restore source does not contain Windows Boot Manager.");
        var previousManager = before.SingleOrDefault(record => record.Id == Bcd.Manager) ?? throw new IOException("The target Windows Boot Manager is missing.");
        ValidateManagerRestore(manager, previousManager);
        var retained = expected.Select(record => record.Id).ToHashSet(StringComparer.OrdinalIgnoreCase);
        try
        {
            foreach (var record in expected.Where(record => record.Id != Bcd.Manager).OrderBy(record => record.Id, StringComparer.OrdinalIgnoreCase))
            {
                try
                {
                    // DeleteExistingObject=2 preserves the concrete source GUID and
                    // replaces all elements, including opaque device/extension data.
                    using var result = Invoke(store, "CopyObject", ("SourceStoreFile", source.file), ("SourceId", record.Id), ("Flags", 2u));
                    if (Bcd.Id((string)((ManagementBaseObject)result["Object"])["Id"]) != record.Id)
                        throw new IOException("Windows returned a different object identifier.");
                }
                catch (Exception error) when (error is InvalidOperationException or ManagementException or IOException)
                { throw new IOException($"Native cross-store BCD replacement failed for {record.Id} (type 0x{record.Type:x8}).", error); }
            }
            RestoreManager(manager, previousManager);
            foreach (var record in before.Where(record => !Firmware(record) && !retained.Contains(record.Id))) Delete(record.Id);
        }
        finally { Refresh(); }
        var after = Enumerate();
        if (!SameRecords(expected, after.Where(record => !Firmware(record)).ToList()))
            throw new IOException("Restored Windows BCD objects did not match the complete protected payload.");
        if (!SameRecords(before.Where(Firmware).ToList(), after.Where(Firmware).ToList()))
            throw new IOException("Native Windows BCD replacement changed the firmware namespace.");
        if (!SameRecords(archived, source.Enumerate()))
            throw new IOException("The private BCD restore source changed during replacement.");
    }
    private static bool ElementChanged(BcdRecord source, BcdRecord target, uint type) =>
        source.RawElements is null || target.RawElements is null ||
        !target.RawElements.TryGetValue(type, out var existing) || existing != source.RawElements[type];
    private static void ValidateManagerRestore(BcdRecord source, BcdRecord target)
    {
        if (source.Type != target.Type || source.RawElements is null || target.RawElements is null)
            throw new IOException("Windows Boot Manager's native object type could not be verified.");
        foreach (var element in source.Elements.Where(element => ElementChanged(source, target, element.Key)))
        {
            var format = (element.Key >> 24) & 0x0f;
            if (format is < 1 or > 7 || format == 1 && element.Value is not BcdDevice { Type: 2 })
                throw new IOException($"Windows Boot Manager element 0x{element.Key:x8} cannot be safely restored in place. Its current firmware registration was preserved.");
        }
    }
    private void RestoreManager(BcdRecord source, BcdRecord target)
    {
        // Never replace/delete {bootmgr}: on UEFI it is also the current firmware
        // registration. Update its individual elements while retaining Boot####.
        foreach (var element in source.Elements.Where(element => ElementChanged(source, target, element.Key)).OrderBy(element => element.Key))
        {
            var type = element.Key;
            switch ((type >> 24) & 0x0f)
            {
                case 1:
                    var device = (BcdDevice)element.Value!;
                    Set(Bcd.Manager, "SetPartitionDeviceElement", ("Type", type), ("DeviceType", device.Type), ("AdditionalOptions", device.AdditionalOptions), ("Path", device.Path));
                    break;
                case 2: String(Bcd.Manager, type, (string)element.Value!); break;
                case 3: Object(Bcd.Manager, type, (string)element.Value!); break;
                case 4: Objects(Bcd.Manager, type, (string[])element.Value!); break;
                case 5: Integer(Bcd.Manager, type, Convert.ToUInt64(element.Value)); break;
                case 6: Boolean(Bcd.Manager, type, Convert.ToBoolean(element.Value)); break;
                case 7: Set(Bcd.Manager, "SetIntegerListElement", ("Type", type), ("Integers", (ulong[])element.Value!)); break;
            }
        }
        foreach (var extra in target.Elements.Keys.Where(type => !source.Elements.ContainsKey(type))) DeleteElement(Bcd.Manager, extra);
    }
    private static bool SameRecords(IReadOnlyCollection<BcdRecord> first, IReadOnlyCollection<BcdRecord> second) =>
        first.Count == second.Count && first.All(record => second.Any(other => record.Id == other.Id && record.Type == other.Type &&
            record.RawElements is not null && other.RawElements is not null &&
            record.RawElements.OrderBy(element => element.Key).SequenceEqual(other.RawElements.OrderBy(element => element.Key))));
    internal static string SystemHivePath() => Registry.GetValue(@"HKEY_LOCAL_MACHINE\SYSTEM\CurrentControlSet\Control\hivelist", @"\REGISTRY\MACHINE\BCD00000000", null) as string ?? "";
    internal static bool IsSystemHiveFile(string path)
    {
        var hive = SystemHivePath();
        if (hive.Length == 0) return true; // Keep the private working file if its lifetime cannot be established.
        var full = System.IO.Path.GetFullPath(path); var root = System.IO.Path.GetPathRoot(full)!;
        var native = WindowsMachine.NativeVolume(root.TrimEnd('\\')) + "\\" + System.IO.Path.GetRelativePath(root, full);
        return hive.Equals(native, StringComparison.OrdinalIgnoreCase) || hive.Equals(full, StringComparison.OrdinalIgnoreCase);
    }
    public void Dispose() { store.Dispose(); storeClass.Dispose(); cleanup?.Invoke(); }
}
