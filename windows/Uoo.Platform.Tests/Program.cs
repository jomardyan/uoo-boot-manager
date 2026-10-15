using System.Diagnostics;
using System.Buffers.Binary;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.Json;
using Uoo.Core;
using Uoo.Platform;

if (!OperatingSystem.IsWindows()) { Console.Error.WriteLine("Native BCD tests require Windows."); return 2; }
if (!WindowsMachine.Administrator) { Console.Error.WriteLine("Native BCD tests require an elevated disposable test machine."); return 2; }
if (args.Length == 2 && args[0] == "--live-integration") return await LiveTransactions.RunAsync(args[1]);
if (args.Length != 0) { Console.Error.WriteLine("Unsupported native test arguments."); return 2; }
int passed = 0, failed = 0;
void Check(bool condition, string message = "Assertion failed") { if (!condition) throw new Exception(message); }
void Test(string name, Action test)
{
    try { test(); Console.WriteLine("PASS " + name); passed++; }
    catch (Exception error) { Console.Error.WriteLine("FAIL " + name + ": " + error); failed++; }
}
string SystemBcd()
{
    var info = new ProcessStartInfo(Path.Combine(Environment.SystemDirectory, "bcdedit.exe")) { UseShellExecute = false, RedirectStandardOutput = true, RedirectStandardError = true, CreateNoWindow = true };
    foreach (var value in new[] { "/enum", "all", "/v" }) info.ArgumentList.Add(value);
    using var process = Process.Start(info)!;
    var output = process.StandardOutput.ReadToEnd(); var error = process.StandardError.ReadToEnd(); process.WaitForExit();
    if (process.ExitCode != 0) throw new Exception("Read-only BCD baseline failed: " + error);
    return output;
}
string baseline = SystemBcd();
string directory = Path.Combine(Path.GetTempPath(), "uoo-bcd-" + Guid.NewGuid().ToString("N")); Directory.CreateDirectory(directory);
string file = Path.Combine(directory, "isolated.bcd");
byte[] EfiLoadOption()
{
    using var output = new MemoryStream(); using var writer = new BinaryWriter(output, Encoding.Unicode);
    var description = Encoding.Unicode.GetBytes("Windows Boot Manager\0");
    var loader = Encoding.Unicode.GetBytes(@"\EFI\Microsoft\Boot\bootmgfw.efi" + "\0");
    writer.Write(1u); writer.Write((ushort)(42 + 4 + loader.Length + 4)); writer.Write(description);
    writer.Write((byte)4); writer.Write((byte)1); writer.Write((ushort)42); writer.Write(1u); writer.Write(2048ul); writer.Write(1048576ul);
    writer.Write(Guid.Parse("06e34b02-622e-4162-846d-7043bab8ccbe").ToByteArray()); writer.Write((byte)2); writer.Write((byte)2);
    writer.Write((byte)4); writer.Write((byte)4); writer.Write((ushort)(4 + loader.Length)); writer.Write(loader);
    writer.Write((byte)0x7f); writer.Write((byte)0xff); writer.Write((ushort)4);
    return output.ToArray();
}
byte[] PeImage(Architecture architecture, ushort subsystem = 10)
{
    var data = new byte[1024];
    void U16(int offset, ushort value) => BinaryPrimitives.WriteUInt16LittleEndian(data.AsSpan(offset), value);
    void U32(int offset, uint value) => BinaryPrimitives.WriteUInt32LittleEndian(data.AsSpan(offset), value);
    data[0] = (byte)'M'; data[1] = (byte)'Z'; U32(0x3c, 0x80); data[0x80] = (byte)'P'; data[0x81] = (byte)'E';
    U16(0x84, architecture == Architecture.Arm64 ? (ushort)0xaa64 : (ushort)0x8664); U16(0x86, 1); U16(0x94, 240); U16(0x96, 0x22);
    int optional = 0x98; U16(optional, 0x20b); U32(optional + 16, 4096); U32(optional + 20, 4096);
    BinaryPrimitives.WriteUInt64LittleEndian(data.AsSpan(optional + 24), 0x140000000); U32(optional + 32, 4096); U32(optional + 36, 512);
    U32(optional + 56, 8192); U32(optional + 60, 512); U16(optional + 68, subsystem); U32(optional + 108, 16);
    int section = optional + 240; Encoding.ASCII.GetBytes(".text").CopyTo(data, section); U32(section + 8, 4096); U32(section + 12, 4096);
    U32(section + 16, 512); U32(section + 20, 512); U32(section + 36, 0x60000020);
    return data;
}
Test("UEFI current-entry parser verifies GPT identity and loader path", () =>
{
    var identity = FirmwareCurrent.Parse(EfiLoadOption());
    Check(identity.PartitionGuid == "{06e34b02-622e-4162-846d-7043bab8ccbe}" && identity.LoaderPath == @"\EFI\Microsoft\Boot\bootmgfw.efi");
});
Test("UEFI parser refuses malformed or truncated load options", () =>
{
    var valid = EfiLoadOption();
    foreach (var fixture in new[] { Array.Empty<byte>(), valid[..10], valid[..^1], valid.Concat(new byte[65536]).ToArray() })
    {
        bool refused = false; try { FirmwareCurrent.Parse(fixture); } catch (IOException) { refused = true; } Check(refused);
    }
    var invalidPathLength = (byte[])valid.Clone(); invalidPathLength[4] = 0xff; invalidPathLength[5] = 0xff;
    bool invalid = false; try { FirmwareCurrent.Parse(invalidPathLength); } catch (IOException) { invalid = true; } Check(invalid);
});
Test("PE validation accepts native 64-bit EFI images only", () =>
{
    foreach (var architecture in new[] { Architecture.X64, Architecture.Arm64 })
    {
        using var valid = new MemoryStream(PeImage(architecture)); WindowsMachine.ValidateEfiImage(valid, architecture);
        using var wrongArchitecture = new MemoryStream(PeImage(architecture));
        bool rejected = false; try { WindowsMachine.ValidateEfiImage(wrongArchitecture, architecture == Architecture.X64 ? Architecture.Arm64 : Architecture.X64); } catch (ArgumentException) { rejected = true; } Check(rejected);
    }
});
Test("PE validation rejects ordinary Windows and truncated executables", () =>
{
    foreach (var fixture in new[] { PeImage(Architecture.X64, 3), PeImage(Architecture.X64)[..64], new byte[1024] })
    {
        using var stream = new MemoryStream(fixture);
        bool refused = false; try { WindowsMachine.ValidateEfiImage(stream, Architecture.X64); } catch (ArgumentException) { refused = true; } Check(refused);
    }
});
Test("EFI creation undo requires current identity or a matching proven boot session", () =>
{
    string created = Guid.NewGuid().ToString("B"), other = Guid.NewGuid().ToString("B");
    WindowsBootService.ValidateCreatedFirmwareUndo(created, other, "", "");
    WindowsBootService.ValidateCreatedFirmwareUndo(created, "", "same-session", "same-session");
    foreach (var parameters in new[] { (created, "same", "same"), ("", "", ""), ("", "old", "new") })
    {
        bool refused = false;
        try { WindowsBootService.ValidateCreatedFirmwareUndo(created, parameters.Item1, parameters.Item2, parameters.Item3); }
        catch (IOException) { refused = true; }
        Check(refused);
    }
});
Test("backup restore preserves existing integrity flags and rejects newly enabled bypasses", () =>
{
    string id = Guid.NewGuid().ToString("B");
    void Refuses(IReadOnlyCollection<BcdRecord> saved, IReadOnlyCollection<BcdRecord> live)
    {
        bool refused = false; try { WindowsBootService.ValidateIntegrityRestore(saved, live); } catch (IOException) { refused = true; }
        Check(refused, "A newly introduced integrity bypass was accepted.");
    }
    foreach (uint flag in new uint[] { 0x16000048, 0x16000049, 0x26000027, 0x260000e1 })
    {
        var on = new BcdRecord(id, Bcd.WindowsLoader, new() { [flag] = true });
        var off = new BcdRecord(id, Bcd.WindowsLoader, new() { [flag] = false });
        var absent = new BcdRecord(id, Bcd.WindowsLoader, new());
        WindowsBootService.ValidateIntegrityRestore([on], [on]);
        WindowsBootService.ValidateIntegrityRestore([off], [on]);
        WindowsBootService.ValidateIntegrityRestore([absent], [on]);
        Refuses([on], [off]); Refuses([on], [absent]); Refuses([on], []);
        Refuses([on], [on with { Type = Bcd.FirmwareApplication }]);
    }
});
Test("backup restore rejects newly exposed inherited integrity flags", () =>
{
    string parentId = Guid.NewGuid().ToString("B"), childId = Guid.NewGuid().ToString("B");
    const uint flag = 0x16000048, inherit = 0x14000006;
    var parent = new BcdRecord(parentId, 0x20100000, new() { [flag] = true });
    var child = new BcdRecord(childId, Bcd.WindowsLoader, new() { [inherit] = new[] { parentId } });
    WindowsBootService.ValidateIntegrityRestore([parent, child], [parent, child]);
    foreach (var previous in new[] { child with { Elements = new() }, child with { Elements = new() { [inherit] = new[] { parentId }, [flag] = false } } })
    {
        bool refused = false;
        try { WindowsBootService.ValidateIntegrityRestore([parent, child], [parent, previous]); } catch (IOException) { refused = true; }
        Check(refused, "A restored inheritance edge introduced a bypass.");
    }
});
try
{
    using var bcd = BcdStore.CreateOffline(file);
    string loader = Guid.NewGuid().ToString("B"), other = Guid.NewGuid().ToString("B");
    Test("create actual offline BCD objects", () =>
    {
        Check(bcd.Create(Bcd.Manager, 0x10100002) == Bcd.Manager);
        Check(bcd.Create(loader, Bcd.WindowsLoader) == loader);
        Check(bcd.Create(other, Bcd.WindowsLoader) == other);
        Check(bcd.Enumerate().Count == 3);
    });
    Test("Windows WMI stores unicode descriptions without a command shell", () =>
    {
        const string name = "Windows — Робота & literal $text"; bcd.String(loader, Bcd.Description, name);
        Check(bcd.Read(loader).Text(Bcd.Description) == name);
    });
    Test("WMI default boot order and timeout round trip", () =>
    {
        bcd.Object(Bcd.Manager, Bcd.Default, loader); bcd.Objects(Bcd.Manager, Bcd.Order, [other, loader]); bcd.Integer(Bcd.Manager, Bcd.Timeout, 17);
        var manager = bcd.Read(Bcd.Manager); Check(manager.Text(Bcd.Default) == loader && manager.Number(Bcd.Timeout) == 17);
        Check(manager.Ids(Bcd.Order).SequenceEqual(new[] { other, loader }));
    });
    Test("WMI graphical menu display flag round trip", () =>
    {
        bcd.Boolean(Bcd.Manager, Bcd.DisplayMenu, false); Check(!bcd.Read(Bcd.Manager).Flag(Bcd.DisplayMenu, true));
        bcd.Boolean(Bcd.Manager, Bcd.DisplayMenu, true); Check(bcd.Read(Bcd.Manager).Flag(Bcd.DisplayMenu));
    });
    Test("WMI one-time choice is removed, not assigned an empty identifier", () =>
    {
        bcd.Objects(Bcd.Manager, Bcd.Sequence, [other]); Check(bcd.Read(Bcd.Manager).Ids(Bcd.Sequence).Single() == other);
        bcd.Objects(Bcd.Manager, Bcd.Sequence, []); Check(!bcd.Read(Bcd.Manager).Elements.ContainsKey(Bcd.Sequence));
    });
    Test("WMI safe mode and alternate shell element types", () =>
    {
        bcd.Integer(loader, Bcd.SafeBoot, 1); bcd.Boolean(loader, 0x26000081, true);
        Check(bcd.Read(loader).Number(Bcd.SafeBoot) == 1 && bcd.Read(loader).Flag(0x26000081));
        bcd.DeleteElement(loader, Bcd.SafeBoot); bcd.DeleteElement(loader, 0x26000081); Check(!bcd.Read(loader).Elements.ContainsKey(Bcd.SafeBoot));
    });
    Test("WMI documented Windows option identifiers", () =>
    {
        foreach (uint element in new[] { Bcd.BootLog, Bcd.Sos, Bcd.BaseVideo, Bcd.Recovery }) { bcd.Boolean(loader, element, true); Check(bcd.Read(loader).Flag(element)); }
        bcd.Integer(loader, Bcd.MenuPolicy, 0); Check(bcd.Read(loader).Number(Bcd.MenuPolicy, 9) == 0);
        bcd.Integer(loader, Bcd.MenuPolicy, 1); Check(bcd.Read(loader).Number(Bcd.MenuPolicy) == 1);
    });
    Test("WMI partition device with no arbitrary paths evaluated", () =>
    {
        var device = WindowsMachine.NativeVolume(Path.GetPathRoot(Environment.SystemDirectory)!.TrimEnd('\\'));
        bcd.Partition(loader, device); bcd.String(loader, Bcd.Path, @"\Windows\system32\winload.efi");
        var model = bcd.Read(loader); Check(model.Device?.Type == 2 && model.Device.Path == device);
        Check(model.Text(Bcd.Path) == @"\Windows\system32\winload.efi");
    });
    Test("WMI copy preserves data and creates a distinct GUID", () =>
    {
        var copied = bcd.Copy(loader); Check(copied != loader && copied != other);
        Check(bcd.Read(copied).Text(Bcd.Description) == bcd.Read(loader).Text(Bcd.Description));
        bcd.Delete(copied); Check(bcd.Enumerate().All(x => x.Id != copied));
    });
    Test("WMI isolated BCD survives reopen", () =>
    {
        using var reopened = new BcdStore(file); Check(reopened.Read(Bcd.Manager).Number(Bcd.Timeout) == 17);
    });
    Test("cross-store restore replaces complete Windows payloads and retains manager identity", () =>
    {
        using var target = BcdStore.CreateOffline(Path.Combine(directory, "restore-target.bcd"));
        string extra = Guid.NewGuid().ToString("B");
        target.Create(Bcd.Manager, 0x10100002); target.Create(extra, Bcd.WindowsLoader);
        target.Integer(Bcd.Manager, Bcd.Timeout, 99);
        target.String(Bcd.Manager, Bcd.Path, @"\EFI\fixture-extra.efi");
        target.Objects(Bcd.Manager, Bcd.Order, [extra]); target.Object(Bcd.Manager, Bcd.Default, extra);
        target.RestoreCoreFrom(bcd);
        Check(target.Read(Bcd.Manager).Number(Bcd.Timeout) == 17);
        Check(target.Read(Bcd.Manager).Text(Bcd.Path) == bcd.Read(Bcd.Manager).Text(Bcd.Path));
        Check(target.Read(Bcd.Manager).Ids(Bcd.Order).SequenceEqual(bcd.Read(Bcd.Manager).Ids(Bcd.Order)));
        Check(target.Read(loader).Device == bcd.Read(loader).Device);
        Check(target.Read(loader).Text(Bcd.Description) == bcd.Read(loader).Text(Bcd.Description));
        Check(target.Enumerate().All(record => record.Id != extra));
        Check(target.Enumerate().Count == bcd.Enumerate().Count);
    });
    Test("offline factory refuses existing store", () =>
    {
        bool refused = false; try { using var invalid = BcdStore.CreateOffline(file); } catch (ArgumentException) { refused = true; } Check(refused);
    });
    Test("current loader alias resolves on the running Windows machine, read only", () =>
    {
        using var live = new BcdStore(); var current = live.Read(Bcd.Current); Check(current.Type == Bcd.WindowsLoader);
        Check(live.Enumerate().Any(x => x.Id == current.Id));
    });
    Test("running Windows loader and kernel resolve through native local volume identity", () =>
    {
        using var live = new BcdStore(); WindowsMachine.ValidateWindowsLoader(live.Read(Bcd.Current));
    });
    Test("Windows loader target preflight rejects disconnected partitions", () =>
    {
        using var live = new BcdStore(); var current = live.Read(Bcd.Current);
        var elements = new Dictionary<uint, object?>(current.Elements) { [Bcd.Device] = new BcdDevice(2, @"\Device\HarddiskVolume999999", "") };
        bool refused = false;
        try { WindowsMachine.ValidateWindowsLoader(current with { Elements = elements }); }
        catch (Exception ex) when (ex is ArgumentException or IOException or InvalidOperationException) { refused = true; }
        Check(refused);
    });
    Test("Windows loader target preflight rejects missing files and traversal", () =>
    {
        using var live = new BcdStore(); var current = live.Read(Bcd.Current);
        foreach (var change in new[] { (Bcd.Path, @"\Windows\system32\uoo-nonexistent-loader.efi"), (Bcd.SystemRoot, @"\Windows\..\Other") })
        {
            var elements = new Dictionary<uint, object?>(current.Elements) { [change.Item1] = change.Item2 };
            bool refused = false;
            try { WindowsMachine.ValidateWindowsLoader(current with { Elements = elements }); }
            catch (Exception ex) when (ex is ArgumentException or IOException or InvalidOperationException) { refused = true; }
            Check(refused);
        }
    });
    Test("real system snapshot includes verified current Windows entry", () =>
    {
        // Inspect is the only service operation invoked against the system store.
        var response = new WindowsBootService().ExecuteAsync(new() { Operation = BootOperation.Inspect }).GetAwaiter().GetResult();
        Check(response.Success && response.Snapshot is not null, response.Message);
        Check(response.Snapshot!.Entries.Count(x => x.IsCurrent && x.IsWindows) == 1, string.Join("; ", response.Snapshot.Errors));
        Check(response.Snapshot.Menu.Order.Count > 0);
        Console.WriteLine(JsonSerializer.Serialize(new { response.Snapshot.WindowsVersion, response.Snapshot.Firmware.Available, response.Snapshot.Repair, response.Snapshot.Diagnostics }, BootJson.Options));
    });
    Test("native private backup reading preserves original integrity without boot writes", () =>
    {
        var service = new WindowsBootService();
        var before = service.ExecuteAsync(new() { Operation = BootOperation.Inspect }).GetAwaiter().GetResult().Snapshot!;
        var response = service.ExecuteAsync(new() { Operation = BootOperation.CreateBackup, ExpectedRevision = before.Revision, Description = "CI native backup validation" }).GetAwaiter().GetResult();
        Check(response.Success && response.Snapshot is not null, response.Message);
        var record = response.Snapshot!.Backups.Single(x => before.Backups.All(old => old.Id != x.Id));
        try
        {
            Check(record.CanRestore, record.Detail);
            var storage = new ProtectedStore(); var manifest = storage.Read(record.Id);
            Check(manifest.CurrentId == before.Entries.Single(x => x.IsCurrent).Id);
            using (var archived = storage.OpenBcd(manifest))
            {
                Check(archived.Read(Bcd.Manager).Ids(Bcd.Order).SequenceEqual(before.Menu.Order));
            }
            Check(storage.Read(record.Id).BcdSha256 == manifest.BcdSha256, "Reading the archived BCD changed its integrity.");
        }
        finally
        {
            var deleted = service.ExecuteAsync(new() { Operation = BootOperation.DeleteBackup, ExpectedRevision = response.Snapshot.Revision, BackupId = record.Id }).GetAwaiter().GetResult();
            Check(deleted.Success && !deleted.Snapshot!.Backups.Any(x => x.Id == record.Id), deleted.Message);
        }
    });
    Test("native provider rejects malformed configuration before any backup or write", () =>
    {
        var service = new WindowsBootService();
        var before = service.ExecuteAsync(new() { Operation = BootOperation.Inspect }).GetAwaiter().GetResult().Snapshot!;
        foreach (var json in new[] { "{\"format\":\"other\"}", "{\"entries\":[null]}", "{\"entries\":null}", "{\"format\":\"a\",\"format\":\"b\"}", "{\"unknown\":\"C:\\\\Windows\"}" })
        {
            var response = service.ExecuteAsync(new() { Operation = BootOperation.ImportConfiguration, ExpectedRevision = before.Revision, ConfigurationJson = json, RecoveryKeyAcknowledged = true }).GetAwaiter().GetResult();
            Check(!response.Success, "Invalid configuration was accepted: " + json);
            Check(response.Snapshot is not null && response.Snapshot.Revision == before.Revision && response.Snapshot.Backups.Count == before.Backups.Count, response.Message);
        }
    });
}
finally
{
    Test("system BCD unchanged by all offline and read-only tests", () => Check(SystemBcd() == baseline));
    try { Directory.Delete(directory, true); } catch (IOException) { /* WMI may release its hive handle asynchronously. */ }
}
Console.WriteLine($"{passed} native Windows tests passed, {failed} failed");
return failed == 0 ? 0 : 1;
