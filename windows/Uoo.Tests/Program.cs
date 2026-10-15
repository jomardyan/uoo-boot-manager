using System.Buffers.Binary;
using System.Text.Json;
using Uoo.Core;
using Uoo.Platform;

int passed = 0, failed = 0;
async Task Test(string name, Func<Task> test)
{
    try { await test(); Console.WriteLine("PASS " + name); passed++; }
    catch (Exception ex) { Console.Error.WriteLine("FAIL " + name + ": " + ex); failed++; }
}
void Check(bool condition, string message = "Assertion failed") { if (!condition) throw new Exception(message); }
async Task<BootSnapshot> Inspect(IBootService service)
{
    var result = await service.ExecuteAsync(new() { Operation = BootOperation.Inspect });
    Check(result.Success && result.Snapshot is not null, result.Message);
    return result.Snapshot!;
}
async Task<BootResponse> Send(IBootService service, BootRequest request)
{
    var current = await Inspect(service);
    return await service.ExecuteAsync(request with { ExpectedRevision = current.Revision });
}
async Task<BootSnapshot> Apply(IBootService service, BootRequest request)
{
    var result = await Send(service, request);
    Check(result.Success && result.Snapshot is not null, result.Message);
    return result.Snapshot!;
}
async Task Reject(BootRequest request)
{
    var service = new DemoBootService();
    var before = await Inspect(service);
    var result = await Send(service, request);
    Check(!result.Success, "Invalid operation was accepted");
    var after = await Inspect(service);
    Check(before.Revision == after.Revision, "Rejected operation changed revision");
    Check(before.Backups.Count == after.Backups.Count, "Rejected operation created a backup");
}

await Test("demo inspection is isolated from callers", async () =>
{
    var service = new DemoBootService(); var snapshot = await Inspect(service);
    snapshot.Entries.Clear(); snapshot.Menu.Order.Clear(); snapshot.Firmware.Entries.Clear();
    var again = await Inspect(service);
    Check(again.Entries.Count == 2 && again.Menu.Order.Count == 2 && again.Firmware.Entries.Count == 2);
});
await Test("menu applies default timeout order and appearance", async () =>
{
    var service = new DemoBootService(); var before = await Inspect(service);
    var state = await Apply(service, new() { Operation = BootOperation.SetMenu, Menu = before.Menu with { DefaultId = DemoBootService.OtherWindowsId, Timeout = 12, DisplayMenu = false, Order = [DemoBootService.OtherWindowsId, DemoBootService.WindowsId] } });
    Check(state.Menu.DefaultId == DemoBootService.OtherWindowsId && state.Menu.Timeout == 12 && !state.Menu.DisplayMenu);
    Check(state.Entries.Single(x => x.IsDefault).Id == DemoBootService.OtherWindowsId && state.Backups.Count == 1);
});
foreach (int timeout in new[] { -1, 301, int.MaxValue })
    await Test("reject timeout " + timeout, () => Reject(new() { Operation = BootOperation.SetMenu, Menu = new() { DefaultId = DemoBootService.WindowsId, Timeout = timeout, Order = [DemoBootService.WindowsId] } }));
await Test("reject menu without running Windows", () => Reject(new() { Operation = BootOperation.SetMenu, Menu = new() { DefaultId = DemoBootService.OtherWindowsId, Order = [DemoBootService.OtherWindowsId] } }));
await Test("reject default outside menu", () => Reject(new() { Operation = BootOperation.SetMenu, Menu = new() { DefaultId = DemoBootService.OtherWindowsId, Order = [DemoBootService.WindowsId] } }));
await Test("reject duplicate menu entry", () => Reject(new() { Operation = BootOperation.SetMenu, Menu = new() { DefaultId = DemoBootService.WindowsId, Order = [DemoBootService.WindowsId, DemoBootService.WindowsId] } }));
await Test("reject obsolete revision", async () =>
{
    var service = new DemoBootService(); var old = await Inspect(service);
    await Apply(service, new() { Operation = BootOperation.RenameEntry, TargetId = DemoBootService.OtherWindowsId, Description = "Changed elsewhere" });
    var response = await service.ExecuteAsync(new() { Operation = BootOperation.SetMenu, ExpectedRevision = old.Revision, Menu = old.Menu });
    Check(!response.Success && response.Message.Contains("Reload", StringComparison.OrdinalIgnoreCase));
});
await Test("reject missing revision", async () =>
{
    var response = await new DemoBootService().ExecuteAsync(new() { Operation = BootOperation.CreateBackup });
    Check(!response.Success);
});
await Test("entry option round trip", async () =>
{
    var service = new DemoBootService();
    var options = new BootEntryOptions { SafeBoot = "AlternateShell", BootLog = true, Sos = true, BaseVideo = true, RecoveryEnabled = false, MenuPolicy = "Legacy" };
    var state = await Apply(service, new() { Operation = BootOperation.SetEntryOptions, TargetId = DemoBootService.OtherWindowsId, Options = options });
    Check(state.Entries.Single(x => x.Id == DemoBootService.OtherWindowsId).Options == options);
});
await Test("reject unsupported SafeBoot", () => Reject(new() { Operation = BootOperation.SetEntryOptions, TargetId = DemoBootService.OtherWindowsId, Options = new() { SafeBoot = "cmd /c anything" } }));
await Test("reject unsupported menu policy", () => Reject(new() { Operation = BootOperation.SetEntryOptions, TargetId = DemoBootService.OtherWindowsId, Options = new() { MenuPolicy = "custom" } }));
await Test("rename unicode Windows entry", async () =>
{
    var state = await Apply(new DemoBootService(), new() { Operation = BootOperation.RenameEntry, TargetId = DemoBootService.OtherWindowsId, Description = "Windows — Робота" });
    Check(state.Entries.Single(x => x.Id == DemoBootService.OtherWindowsId).Description == "Windows — Робота");
});
foreach (string badName in new[] { "", " \t", "Windows\nother", new string('a', 121) })
    await Test("reject invalid entry name " + JsonSerializer.Serialize(badName), () => Reject(new() { Operation = BootOperation.RenameEntry, TargetId = DemoBootService.OtherWindowsId, Description = badName }));
await Test("copy then delete only a new entry", async () =>
{
    var service = new DemoBootService(); var copied = await Apply(service, new() { Operation = BootOperation.CopyEntry, TargetId = DemoBootService.WindowsId, Description = "Recovery profile" });
    var entry = copied.Entries.Single(x => x.Description == "Recovery profile");
    Check(!entry.IsCurrent && !entry.IsDefault && copied.Menu.Order.Contains(entry.Id));
    var removed = await Apply(service, new() { Operation = BootOperation.DeleteEntry, TargetId = entry.Id });
    Check(removed.Entries.Count == 2 && !removed.Menu.Order.Contains(entry.Id));
});
await Test("protect current boot entry", () => Reject(new() { Operation = BootOperation.DeleteEntry, TargetId = DemoBootService.WindowsId }));
await Test("protect default after selecting another default", async () =>
{
    var service = new DemoBootService(); var before = await Inspect(service);
    await Apply(service, new() { Operation = BootOperation.SetMenu, Menu = before.Menu with { DefaultId = DemoBootService.OtherWindowsId } });
    Check(!(await Send(service, new() { Operation = BootOperation.DeleteEntry, TargetId = DemoBootService.OtherWindowsId })).Success);
});
await Test("Windows once protects scheduled target and clears", async () =>
{
    var service = new DemoBootService(); var once = await Apply(service, new() { Operation = BootOperation.SetBootOnce, TargetId = DemoBootService.OtherWindowsId });
    Check(once.Menu.BootOnceId == DemoBootService.OtherWindowsId);
    Check(!(await Send(service, new() { Operation = BootOperation.DeleteEntry, TargetId = DemoBootService.OtherWindowsId })).Success);
    var clear = await Apply(service, new() { Operation = BootOperation.ClearBootOnce }); Check(clear.Menu.BootOnceId == "");
});
await Test("firmware reorder preserves entries", async () =>
{
    var state = await Apply(new DemoBootService(), new() { Operation = BootOperation.SetFirmwareOrder, Order = [DemoBootService.FirmwareUbuntuId, DemoBootService.FirmwareWindowsId] });
    Check(state.Firmware.Order[0] == DemoBootService.FirmwareUbuntuId && state.Firmware.Entries.Count == 2);
});
await Test("explicit firmware exclusion preserves registration", async () =>
{
    var state = await Apply(new DemoBootService(), new() { Operation = BootOperation.SetFirmwareOrder, Order = [DemoBootService.FirmwareWindowsId] });
    Check(state.Firmware.Entries.Count == 2 && state.Firmware.Order.Count == 1);
});
await Test("protect current firmware order inclusion", () => Reject(new() { Operation = BootOperation.SetFirmwareOrder, Order = [DemoBootService.FirmwareUbuntuId] }));
await Test("protect Windows Boot Manager", () => Reject(new() { Operation = BootOperation.DeleteFirmwareEntry, TargetId = DemoBootService.FirmwareWindowsId }));
await Test("firmware once and clear", async () =>
{
    var service = new DemoBootService(); var state = await Apply(service, new() { Operation = BootOperation.SetFirmwareBootOnce, TargetId = DemoBootService.FirmwareUbuntuId });
    Check(state.Firmware.BootOnceId == DemoBootService.FirmwareUbuntuId);
    Check(!(await Send(service, new() { Operation = BootOperation.DeleteFirmwareEntry, TargetId = DemoBootService.FirmwareUbuntuId })).Success);
    state = await Apply(service, new() { Operation = BootOperation.ClearFirmwareBootOnce }); Check(state.Firmware.BootOnceId == "");
});
await Test("add EFI registration then delete", async () =>
{
    var service = new DemoBootService(); var state = await Apply(service, new() { Operation = BootOperation.AddFirmwareEntry, Description = "Linux", EfiPartition = @"S:\", EfiLoaderPath = @"\EFI\Linux\shimx64.efi" });
    var entry = state.Firmware.Entries.Single(x => x.Description == "Linux");
    Check(state.Firmware.Order.Contains(entry.Id));
    state = await Apply(service, new() { Operation = BootOperation.DeleteFirmwareEntry, TargetId = entry.Id }); Check(state.Firmware.Entries.Count == 2);
});
foreach (string path in new[] { @"\EFI\..\Windows\boot.efi", @"\Windows\winload.efi", @"\EFI\boot\boot.exe", @"\\server\boot.efi" })
    await Test("reject EFI loader " + path, () => Reject(new() { Operation = BootOperation.AddFirmwareEntry, Description = "Test", EfiPartition = @"S:\", EfiLoaderPath = path }));
await Test("reject remote ESP", () => Reject(new() { Operation = BootOperation.AddFirmwareEntry, Description = "Test", EfiPartition = @"\\server\share\", EfiLoaderPath = @"\EFI\boot\bootx64.efi" }));
await Test("manual backup restore keeps pre-restore recovery point", async () =>
{
    var service = new DemoBootService(); var backedUp = await Apply(service, new() { Operation = BootOperation.CreateBackup }); var id = backedUp.Backups[0].Id;
    await Apply(service, new() { Operation = BootOperation.RenameEntry, TargetId = DemoBootService.OtherWindowsId, Description = "New name" });
    var restored = await Apply(service, new() { Operation = BootOperation.RestoreBackup, BackupId = id });
    Check(restored.Entries.Single(x => x.Id == DemoBootService.OtherWindowsId).Description == "Windows 11 — Work");
    Check(restored.Backups.Count == 3);
    restored = await Apply(service, new() { Operation = BootOperation.DeleteBackup, BackupId = id }); Check(!restored.Backups.Any(x => x.Id == id));
});
await Test("reject backup path traversal", () => Reject(new() { Operation = BootOperation.RestoreBackup, BackupId = "../Windows" }));
await Test("repair requires exact reviewed confirmation", () => Reject(new() { Operation = BootOperation.Repair, Confirmation = "repair" }));
await Test("demo repair creates recovery backup", async () =>
{
    var state = await Apply(new DemoBootService(), new() { Operation = BootOperation.Repair, Confirmation = "REPAIR" }); Check(state.Backups.Count == 1 && state.Demo);
});
await Test("configuration export import and invalid JSON atomicity", async () =>
{
    var service = new DemoBootService(); var directory = Path.Combine(Path.GetTempPath(), "uoo-tests-" + Guid.NewGuid().ToString("N")); Directory.CreateDirectory(directory);
    try
    {
        var path = Path.Combine(directory, "configuration.json");
        await Apply(service, new() { Operation = BootOperation.ExportConfiguration, FilePath = path });
        await Apply(service, new() { Operation = BootOperation.RenameEntry, TargetId = DemoBootService.OtherWindowsId, Description = "Changed" });
        var restored = await Apply(service, new() { Operation = BootOperation.ImportConfiguration, FilePath = path });
        Check(restored.Entries.Single(x => x.Id == DemoBootService.OtherWindowsId).Description == "Windows 11 — Work");
        await File.WriteAllTextAsync(path, "{\"platform\":\"linux\",\"arbitrary\":true}");
        var rejected = await Send(service, new() { Operation = BootOperation.ImportConfiguration, FilePath = path });
        Check(!rejected.Success && (await Inspect(service)).Revision == restored.Revision);
    }
    finally { Directory.Delete(directory, true); }
});
await Test("JSON protocol rejects unrecognized properties and numeric operations", () =>
{
    foreach (var json in new[] { "{\"operation\":\"Inspect\",\"command\":\"anything\"}", "{\"operation\":0}" })
    {
        bool rejected = false; try { JsonSerializer.Deserialize<BootRequest>(json, BootJson.Options); } catch (JsonException) { rejected = true; } Check(rejected);
    }
    return Task.CompletedTask;
});
await Test("pipe framing UTF-8 round trip", async () =>
{
    using var stream = new MemoryStream(); const string json = "{\"message\":\"Робота ✓\"}";
    await PipeProtocol.WriteAsync(stream, json, default); stream.Position = 0; Check(await PipeProtocol.ReadAsync(stream, default) == json);
});
await Test("GUID matching preserves case-insensitive Windows semantics", async () =>
{
    var service = new DemoBootService();
    var renamed = await Apply(service, new() { Operation = BootOperation.RenameEntry, TargetId = DemoBootService.OtherWindowsId.ToUpperInvariant(), Description = "Uppercase selection" });
    Check(renamed.Entries.Single(x => x.Id == DemoBootService.OtherWindowsId).Description == "Uppercase selection");
    var copied = await Apply(service, new() { Operation = BootOperation.CopyEntry, TargetId = DemoBootService.WindowsId.ToUpperInvariant(), Description = "Case-insensitive copy" });
    Check(copied.Entries.Count == 3);
});
await Test("reject null, duplicate, and one-time imported configuration atomically", async () =>
{
    var service = new DemoBootService(); var before = await Inspect(service);
    var menuJson = JsonSerializer.Serialize(before.Menu with { BootOnceId = DemoBootService.OtherWindowsId }, BootJson.Options);
    foreach (var json in new[] { "{\"entries\":[null]}", "{\"entries\":null}", "{\"menu\":null}", "{\"version\":1,\"version\":1}", "{\"menu\":" + menuJson + "}" })
    {
        var response = await Send(service, new() { Operation = BootOperation.ImportConfiguration, ConfigurationJson = json });
        Check(!response.Success && (await Inspect(service)).Revision == before.Revision && (await Inspect(service)).Backups.Count == 0);
    }
});
await Test("backup restore preserves later firmware registrations and can undo a specific creation", async () =>
{
    var service = new DemoBootService();
    var initial = await Apply(service, new() { Operation = BootOperation.CreateBackup }); string manual = initial.Backups[0].Id;
    var added = await Apply(service, new() { Operation = BootOperation.AddFirmwareEntry, Description = "Later EFI registration", EfiPartition = @"S:\", EfiLoaderPath = @"\EFI\later\boot.efi" });
    string undo = added.Backups[0].Id;
    var restored = await Apply(service, new() { Operation = BootOperation.RestoreBackup, BackupId = manual });
    Check(restored.Firmware.Entries.Any(x => x.Description == "Later EFI registration"));
    var undone = await Apply(service, new() { Operation = BootOperation.RestoreBackup, BackupId = undo });
    Check(!undone.Firmware.Entries.Any(x => x.Description == "Later EFI registration"));
});
await Test("existing non-Windows boot-manager entries remain selectable", () =>
{
    var legacy = new BootEntry { Id = "{797fd7a2-fbe0-4bd0-b144-c71cab4719e6}", Description = "Existing boot application", IsWindows = false };
    var state = new BootSnapshot { Revision = "fixture", Entries = [new() { Id = DemoBootService.WindowsId, Description = "Windows", IsWindows = true, IsCurrent = true }, legacy] };
    BootValidation.Validate(new() { Operation = BootOperation.SetMenu, ExpectedRevision = "fixture", Menu = new() { DefaultId = legacy.Id, Order = [legacy.Id, DemoBootService.WindowsId] } }, state);
    BootValidation.Validate(new() { Operation = BootOperation.SetBootOnce, ExpectedRevision = "fixture", TargetId = legacy.Id }, state);
    return Task.CompletedTask;
});
await Test("BitLocker acknowledgment follows live known protection state", () =>
{
    Check(!BootValidation.RequiresRecoveryKey(new() { Demo = true, BitLockerProtectionEnabled = true }));
    Check(BootValidation.RequiresRecoveryKey(new() { BitLockerProtectionEnabled = true }));
    Check(BootValidation.RequiresRecoveryKey(new() { BitLockerStatusKnown = false }));
    Check(!BootValidation.RequiresRecoveryKey(new() { BitLockerProtectionEnabled = false, BitLockerStatusKnown = true }));
    return Task.CompletedTask;
});
await Test("unknown firmware current identity permits reordering but preserves every saved entry", () =>
{
    var first = DemoBootService.FirmwareWindowsId; var second = DemoBootService.FirmwareUbuntuId;
    var state = new BootSnapshot { Revision = "fixture", Firmware = new() { Available = true, CurrentId = "", Entries = [new() { Id = first }, new() { Id = second }], Order = [first, second] } };
    BootValidation.Validate(new() { Operation = BootOperation.SetFirmwareOrder, ExpectedRevision = "fixture", Order = [second, first] }, state);
    bool refused = false;
    try { BootValidation.Validate(new() { Operation = BootOperation.SetFirmwareOrder, ExpectedRevision = "fixture", Order = [first] }, state); }
    catch (ArgumentException) { refused = true; }
    Check(refused);
    return Task.CompletedTask;
});
await Test("pipe framing rejects invalid sizes and truncation", async () =>
{
    foreach (int length in new[] { -1, 0, 1, PipeProtocol.MaximumBytes + 1 })
    {
        using var stream = new MemoryStream(); var header = new byte[4]; BinaryPrimitives.WriteInt32LittleEndian(header, length); stream.Write(header); stream.Position = 0;
        bool rejected = false; try { await PipeProtocol.ReadAsync(stream, default); } catch (InvalidDataException) { rejected = true; } Check(rejected);
    }
    using var shortStream = new MemoryStream(new byte[] { 10, 0, 0, 0, 1 });
    bool truncated = false; try { await PipeProtocol.ReadAsync(shortStream, default); } catch (EndOfStreamException) { truncated = true; } Check(truncated);
});
Console.WriteLine($"{passed} passed, {failed} failed");
return failed == 0 ? 0 : 1;
