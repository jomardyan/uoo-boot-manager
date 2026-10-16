using System.Text.Json;

namespace Uoo.Core;

/// <summary>A full in-memory Windows model. Only explicit JSON import/export touches user files.</summary>
public sealed class DemoBootService : IBootService
{
    public const string WindowsId = "{d331af0d-30c0-4972-81d0-44c03785baf1}";
    public const string OtherWindowsId = "{6276c4d5-3f74-4a37-9695-906359c613c4}";
    public const string FirmwareWindowsId = "{9dea862c-5cdd-4e70-acc1-f32b344d4795}";
    public const string FirmwareUbuntuId = "{91800b3f-34a0-4540-96de-2789d7030791}";
    private BootSnapshot state;
    private readonly Dictionary<string, BootSnapshot> backups = [];
    private readonly Dictionary<string, string> createdFirmware = [];
    private int revision;
    private int backupNumber;
    private readonly SemaphoreSlim gate = new(1, 1);

    public DemoBootService()
    {
        state = new()
        {
            Demo = true, BitLockerProtectionEnabled = true, Revision = "demo-0", Machine = "Windows 11 PC (demo)", WindowsVersion = "Windows 11 24H2 · demo",
            Entries = [
                new() { Id = WindowsId, Description = "Windows 11", Device = @"C:\", Path = @"\Windows\system32\winload.efi", SystemRoot = @"\Windows", IsCurrent = true, IsDefault = true, IsWindows = true },
                new() { Id = OtherWindowsId, Description = "Windows 11 — Work", Device = @"D:\", Path = @"\Windows\system32\winload.efi", SystemRoot = @"\Windows", IsWindows = true }
            ],
            Menu = new() { DefaultId = WindowsId, Timeout = 10, Order = [WindowsId, OtherWindowsId] },
            Firmware = new() { Available = true, Entries = [
                new() { Id = FirmwareWindowsId, Description = "Windows Boot Manager", Device = @"\Device\HarddiskVolume1", Path = @"\EFI\Microsoft\Boot\bootmgfw.efi", IsWindowsBootManager = true, IsCurrent = true },
                new() { Id = FirmwareUbuntuId, Description = "Ubuntu", Device = @"\Device\HarddiskVolume1", Path = @"\EFI\ubuntu\shimx64.efi" }
            ], Order = [FirmwareWindowsId, FirmwareUbuntuId], CurrentId = FirmwareWindowsId },
            Diagnostics = [new("Boot environment", "OK", "UEFI with Secure Boot enabled (demo)."), new("Windows BCD store", "OK", "Two Windows loaders and a valid default entry."), new("BitLocker", "Info", "Protection is enabled. Have your recovery key available before changing boot settings (demo)."), new("Demo mode", "Info", "Boot changes stay in memory for this session. Import and export use only the file you choose.")],
            LinuxLoaders = [new("Ubuntu", @"\\?\Volume{be85a039-98b2-451b-a37f-aa7a87160bb8}\", @"\EFI\ubuntu\shimx64.efi"), new("Fedora", @"\\?\Volume{be85a039-98b2-451b-a37f-aa7a87160bb8}\", @"\EFI\fedora\shimx64.efi")],
            DualBootFacts = new() { FastStartupEnabled = true, ClockIsUtc = false, SecureBootEnabled = true, Esps = [new(@"\\?\Volume{be85a039-98b2-451b-a37f-aa7a87160bb8}\", 62L * 1024 * 1024, 100L * 1024 * 1024)] },
            Repair = new() { Available = true, WindowsDirectory = @"C:\Windows", SystemPartition = @"\\?\Volume{be85a039-98b2-451b-a37f-aa7a87160bb8}\", Description = "Back up BCD and Microsoft EFI files, then rebuild the running Windows installation's UEFI boot files with BCDBoot. Other EFI vendors are preserved.", Confirmation = "REPAIR" }
        };
    }

    public async Task<BootResponse> ExecuteAsync(BootRequest request, CancellationToken cancellationToken = default)
    {
        await gate.WaitAsync(cancellationToken);
        try
        {
            cancellationToken.ThrowIfCancellationRequested();
            BootValidation.Validate(request, state);
            request = Normalize(request);
            if (request.Operation == BootOperation.Inspect) return Response("Demo boot settings loaded.");
            if (request.Operation == BootOperation.ExportConfiguration)
            {
                var json = BootConfiguration.Export(state);
                if (!string.IsNullOrWhiteSpace(request.FilePath)) await BootConfiguration.WriteFileAsync(request.FilePath, json, cancellationToken);
                return Response("Demo configuration exported.") with { ConfigurationJson = json };
            }
            WindowsConfiguration? imported = null;
            if (request.Operation == BootOperation.ImportConfiguration)
            {
                string json = request.ConfigurationJson;
                if (!string.IsNullOrWhiteSpace(request.FilePath))
                    json = await BootConfiguration.ReadFileAsync(request.FilePath, cancellationToken);
                imported = BootConfiguration.Parse(json, state);
            }
            if (request.Operation == BootOperation.RestoreBackup && createdFirmware.TryGetValue(request.BackupId, out var undo) &&
                (state.Firmware.BootOnceId == undo || state.Firmware.CurrentId == undo))
                throw new ArgumentException("Clear the scheduled firmware boot before undoing its registration.");
            string automatic = "";
            if (request.Operation is not (BootOperation.CreateBackup or BootOperation.DeleteBackup))
                automatic = AddBackup("Before " + request.Operation);
            string message;
            switch (request.Operation)
            {
                case BootOperation.SetMenu:
                    state = state with { Menu = BootJson.Clone(request.Menu!) with { BootOnceId = state.Menu.BootOnceId } };
                    state = state with { Entries = state.Entries.Select(x => x with { IsDefault = x.Id == state.Menu.DefaultId }).ToList() };
                    message = "Demo boot menu updated."; break;
                case BootOperation.SetEntryOptions:
                    ChangeEntry(request.TargetId, x => x with { Options = BootJson.Clone(request.Options!) }); message = "Demo entry options updated."; break;
                case BootOperation.RenameEntry:
                    ChangeEntry(request.TargetId, x => x with { Description = BootValidation.Description(request.Description) }); message = "Demo entry renamed."; break;
                case BootOperation.CopyEntry:
                    var source = state.Entries.Single(x => x.Id == request.TargetId);
                    var id = Guid.NewGuid().ToString("B");
                    state.Entries.Add(source with { Id = id, Description = BootValidation.Description(request.Description), IsCurrent = false, IsDefault = false });
                    state.Menu.Order.Add(id); message = "Demo Windows entry copied."; break;
                case BootOperation.DeleteEntry:
                    state.Entries.RemoveAll(x => x.Id == request.TargetId); state.Menu.Order.Remove(request.TargetId); message = "Demo entry removed."; break;
                case BootOperation.SetBootOnce:
                    state = state with { Menu = state.Menu with { BootOnceId = request.TargetId } }; message = "Demo one-time Windows boot scheduled."; break;
                case BootOperation.ClearBootOnce:
                    state = state with { Menu = state.Menu with { BootOnceId = "" } }; message = "Demo one-time Windows boot cleared."; break;
                case BootOperation.SetFirmwareOrder:
                    state = state with { Firmware = state.Firmware with { Order = [.. request.Order] } }; message = "Demo UEFI order updated."; break;
                case BootOperation.SetFirmwareBootOnce:
                    state = state with { Firmware = state.Firmware with { BootOnceId = request.TargetId } }; message = "Demo one-time firmware boot scheduled."; break;
                case BootOperation.ClearFirmwareBootOnce:
                    state = state with { Firmware = state.Firmware with { BootOnceId = "" } }; message = "Demo one-time firmware boot cleared."; break;
                case BootOperation.AddFirmwareEntry:
                    var fwId = Guid.NewGuid().ToString("B");
                    state.Firmware.Entries.Add(new() { Id = fwId, Description = BootValidation.Description(request.Description), Device = request.EfiPartition, Path = request.EfiLoaderPath });
                    state.Firmware.Order.Add(fwId); createdFirmware[automatic] = fwId; message = "Demo EFI registration created."; break;
                case BootOperation.DeleteFirmwareEntry:
                    state.Firmware.Entries.RemoveAll(x => x.Id == request.TargetId); state.Firmware.Order.Remove(request.TargetId); message = "Demo EFI registration removed."; break;
                case BootOperation.CreateBackup:
                    AddBackup(string.IsNullOrWhiteSpace(request.Description) ? "Manual backup" : BootValidation.Description(request.Description)); message = "Demo backup created."; break;
                case BootOperation.RestoreBackup:
                    var restored = BootJson.Clone(backups[request.BackupId]);
                    string removedId = createdFirmware.GetValueOrDefault(request.BackupId, "");
                    var inventory = restored.Firmware.Entries.Concat(state.Firmware.Entries).Where(x => x.Id != removedId).DistinctBy(x => x.Id).ToList();
                    var known = restored.Firmware.Entries.Select(x => x.Id).ToHashSet(StringComparer.OrdinalIgnoreCase);
                    var order = restored.Firmware.Order.Concat(state.Firmware.Order.Where(x => !known.Contains(x)))
                        .Where(x => x != removedId).Distinct(StringComparer.OrdinalIgnoreCase).ToList();
                    state = restored with { Backups = state.Backups, Firmware = restored.Firmware with { Entries = inventory, Order = order } };
                    message = "Demo backup restored; the previous state was backed up first."; break;
                case BootOperation.DeleteBackup:
                    backups.Remove(request.BackupId); createdFirmware.Remove(request.BackupId); state.Backups.RemoveAll(x => x.Id == request.BackupId); message = "Demo backup deleted."; break;
                case BootOperation.ImportConfiguration:
                    state = state with { Menu = BootJson.Clone(imported!.Menu) with { BootOnceId = state.Menu.BootOnceId }, Entries = state.Entries.Select(x =>
                    { var model = imported.Entries.SingleOrDefault(e => e.Id == x.Id); return (model is null ? x : x with { Description = model.Description, Options = BootJson.Clone(model.Options) }) with { IsDefault = x.Id == imported.Menu.DefaultId }; }).ToList() };
                    message = "Demo Windows configuration imported."; break;
                case BootOperation.Repair:
                    message = "Demo repair completed. A recovery backup was created. No real boot files changed."; break;
                default: throw new ArgumentException("Unsupported operation.");
            }
            state = state with { Revision = $"demo-{++revision}" };
            return Response(message);
        }
        catch (Exception error) when (error is ArgumentException or InvalidOperationException or IOException or InvalidDataException or UnauthorizedAccessException or JsonException)
        {
            return new() { Success = false, Message = error.Message, Snapshot = BootJson.Clone(state) };
        }
        finally { gate.Release(); }
    }

    private void ChangeEntry(string id, Func<BootEntry, BootEntry> update) => state = state with { Entries = state.Entries.Select(x => x.Id == id ? update(x) : x).ToList() };
    private string AddBackup(string description)
    {
        string id = $"demo-{++backupNumber:D5}";
        backups[id] = BootJson.Clone(state);
        state.Backups.Insert(0, new() { Id = id, Description = description, CreatedAt = DateTimeOffset.UtcNow, CanRestore = true, Detail = "In-memory BCD and firmware settings." });
        return id;
    }
    private BootResponse Response(string message) => new() { Success = true, Message = message, Snapshot = BootJson.Clone(state) };

    private static BootRequest Normalize(BootRequest request)
    {
        if (request.Operation is BootOperation.SetEntryOptions or BootOperation.RenameEntry or BootOperation.CopyEntry or BootOperation.DeleteEntry or BootOperation.SetBootOnce or BootOperation.SetFirmwareBootOnce or BootOperation.DeleteFirmwareEntry)
            request = request with { TargetId = BootValidation.Id(request.TargetId) };
        if (request.Operation == BootOperation.SetMenu)
            request = request with { Menu = request.Menu! with { DefaultId = BootValidation.Id(request.Menu!.DefaultId), Order = request.Menu.Order.Select(BootValidation.Id).ToList() } };
        if (request.Operation == BootOperation.SetFirmwareOrder) request = request with { Order = request.Order.Select(BootValidation.Id).ToList() };
        return request;
    }

}
