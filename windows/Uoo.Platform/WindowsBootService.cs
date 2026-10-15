using System.ComponentModel;
using System.Management;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using Uoo.Core;

namespace Uoo.Platform;

/// <summary>Native Windows BCD/UEFI provider. Changes require an elevated process.</summary>
public sealed class WindowsBootService : IBootService
{
    private readonly ProtectedStore backups = new();
    private static readonly SemaphoreSlim Gate = new(1, 1);

    public async Task<BootResponse> ExecuteAsync(BootRequest request, CancellationToken cancellationToken = default)
    {
        ArgumentNullException.ThrowIfNull(request);
        if (!OperatingSystem.IsWindows()) return new() { Success = false, Message = "The Windows boot provider is available on Windows 11 only." };
        await Gate.WaitAsync(cancellationToken).ConfigureAwait(false);
        try { return await Task.Run(() => Execute(request, cancellationToken), cancellationToken).ConfigureAwait(false); }
        finally { Gate.Release(); }
    }

    private BootResponse Execute(BootRequest request, CancellationToken token)
    {
        try
        {
            if (request.Operation == BootOperation.Inspect && !WindowsMachine.Administrator)
                return new() { Success = true, Snapshot = Inspect(), Message = "Boot settings inspected. Changes require administrator approval." };
            if (!WindowsMachine.Administrator) throw new UnauthorizedAccessException("Administrator approval is required for this operation.");
            using var transactionLock = backups.Lock();
            using var bcd = new BcdStore();
            var interrupted = backups.PendingId();
            if (interrupted.Length != 0)
            {
                Restore(bcd, backups.Read(interrupted), true); backups.Complete();
                if (request.Operation != BootOperation.Inspect)
                    return new() { Success = false, Snapshot = Inspect(bcd), Message = "An interrupted change was recovered. Reload and review the restored settings before applying another change." };
            }
            var before = Inspect(bcd);
            if (request.Operation == BootOperation.Inspect)
                return new() { Success = true, Snapshot = before, Message = interrupted.Length == 0 ? "Boot settings inspected." : "Interrupted change recovered." };
            if (before.Errors.Count != 0 || before.Entries.Count(e => e.IsCurrent) != 1)
                throw new InvalidOperationException("The running boot configuration could not be verified. No boot settings were changed.");
            BootValidation.Validate(request, before);
            if (!string.IsNullOrEmpty(request.FilePath)) throw new ArgumentException("Privileged file import/export is disabled. Pass configuration JSON through the desktop broker.");
            token.ThrowIfCancellationRequested();
            if (request.Operation == BootOperation.ExportConfiguration)
                return new() { Success = true, Snapshot = before, Message = "Configuration exported.", ConfigurationJson = BootConfiguration.Export(before) };
            if (request.Operation == BootOperation.DeleteBackup)
            { backups.Delete(request.BackupId); return new() { Success = true, Snapshot = Inspect(bcd), Message = "Backup deleted." }; }
            if (request.Operation == BootOperation.CreateBackup)
            {
                var manual = backups.Create(bcd, before, string.IsNullOrWhiteSpace(request.Description) ? "Manual boot configuration backup" : BootValidation.Description(request.Description));
                return new() { Success = true, Snapshot = Inspect(bcd), Message = "Backup created: " + manual.Id };
            }
            // Any other change would fail menu verification after applying; only these operations can repair the menu.
            var menuVerified = Verified(before);
            if (!menuVerified && request.Operation is not (BootOperation.SetMenu or BootOperation.ImportConfiguration or BootOperation.RestoreBackup))
                throw new InvalidOperationException("The boot menu is missing the default or running Windows entry. Set the boot menu, import a configuration, or restore a backup first. No boot settings were changed.");
            var protection = WindowsMachine.BitLocker();
            if (protection.Protection != 0 && !request.RecoveryKeyAcknowledged)
                throw new InvalidOperationException(protection.Protection == 1
                    ? "BitLocker protection is on. Confirm that you have the recovery key before changing boot settings. Uoo will not suspend protection."
                    : "BitLocker status could not be verified. Confirm that you are prepared for recovery-key entry before changing boot settings. Uoo will not change encryption protection.");

            // All request-dependent preflight happens before creating the journal or touching BCD.
            EspPartition? esp = null; FirmwareImage? created = null; BackupManifest? restore = null;
            WindowsConfiguration? configuration = null;
            if (request.Operation == BootOperation.AddFirmwareEntry)
            {
                if (before.Firmware.CurrentId.Length == 0 && WindowsMachine.BootSession().Length == 0)
                    throw new InvalidOperationException("Firmware registration is unavailable because BootCurrent is unknown and this boot session cannot be proven for safe rollback. Hibernation/Fast Startup or unavailable boot information prevents this operation.");
                esp = WindowsMachine.ValidateEsp(request.EfiPartition); WindowsMachine.EfiPath(esp, request.EfiLoaderPath);
                created = new(Guid.NewGuid().ToString("B"), Bcd.FirmwareApplication, BootValidation.Description(request.Description),
                    request.EfiLoaderPath, new(2, esp.Device, ""), esp.Volume);
            }
            if (request.Operation == BootOperation.DeleteFirmwareEntry)
            {
                var target = bcd.Read(request.TargetId);
                var currentFirmware = ResolveCurrentFirmware(before.Firmware.Entries);
                if (currentFirmware.Length == 0) throw new InvalidOperationException("The current firmware boot entry could not be identified. Firmware deletion is disabled.");
                if (currentFirmware.Equals(target.Id, StringComparison.OrdinalIgnoreCase)) throw new ArgumentException("The currently used firmware boot registration cannot be removed.");
                var partition = WindowsMachine.ValidateEsp(target.Device?.Path ?? "");
                ValidateFirmwareRecreation(new(target.Id, target.Type, target.Text(Bcd.Description), target.Text(Bcd.Path), target.Device, partition.Volume));
            }
            if (request.Operation == BootOperation.DeleteEntry)
                ValidateEntryDeletion(bcd.Enumerate(), request.TargetId);
            if (request.Operation == BootOperation.RestoreBackup)
            { restore = backups.Read(request.BackupId); ValidateRestore(bcd, restore, false); }
            if (request.Operation == BootOperation.ImportConfiguration)
                configuration = BootConfiguration.Parse(request.ConfigurationJson, before);
            if (request.Operation == BootOperation.SetMenu)
                WindowsMachine.ValidateWindowsLoader(bcd.Read(request.Menu!.DefaultId));
            if (request.Operation == BootOperation.SetBootOnce)
                WindowsMachine.ValidateWindowsLoader(bcd.Read(request.TargetId));
            if (configuration is not null)
                WindowsMachine.ValidateWindowsLoader(bcd.Read(configuration.Menu.DefaultId));
            if (request.Operation == BootOperation.Repair)
            {
                var plan = WindowsMachine.RepairPlan();
                if (!plan.Available || plan.SystemPartition != before.Repair.SystemPartition || request.Confirmation != plan.Confirmation)
                    throw new InvalidOperationException("The repair plan changed. Reload and review the verified system ESP.");
                esp = WindowsMachine.ValidateEsp(plan.SystemPartition, true);
            }
            var backup = backups.Create(bcd, before, "Before " + request.Operation, created,
                request.Operation == BootOperation.Repair ? esp : restore?.Efi is not null ? WindowsMachine.ValidateEsp(restore.Efi.Volume, true) : null);
            _ = backups.Read(backup.Id);
            if (created is not null && before.Firmware.CurrentId.Length == 0 && backup.BootSession.Length == 0)
                throw new InvalidOperationException("Boot-session proof became unavailable before firmware registration. No boot settings were changed.");
            if (request.Operation == BootOperation.DeleteFirmwareEntry)
                ValidateFirmwareRecreation(backup.Firmware.Single(f => f.Id.Equals(request.TargetId, StringComparison.OrdinalIgnoreCase)));
            if (restore?.CreatedFirmwareId.Length > 0)
            {
                var removal = backup.Firmware.SingleOrDefault(f => f.Id == restore.CreatedFirmwareId);
                if (removal is not null) ValidateFirmwareRecreation(removal);
            }
            backups.Journal(backup.Id);
            List<string> recreated = [];
            try
            {
                Change(bcd, request, before, esp, created, restore, configuration, recreated);
                var after = Inspect(bcd);
                Verify(after);
                VerifyRequested(request, before, after);
                backups.Complete();
                return new() { Success = true, Snapshot = after, Message = "Boot settings updated. Backup: " + backup.Id };
            }
            catch (Exception error)
            {
                try
                {
                    // Registrations re-created by a failed restore did not exist before it.
                    var existing = bcd.Enumerate();
                    foreach (var id in recreated.Where(id => existing.Any(r => r.Id == id))) DeleteFirmware(bcd, id);
                    Restore(bcd, backup, true); Verify(Inspect(bcd), menuVerified); backups.Complete();
                }
                catch (Exception rollback) { throw new IOException("The change failed and recovery is incomplete. The protected journal and backup were retained; reopen as administrator to retry recovery. Change: " + error.Message + " Recovery: " + rollback.Message, rollback); }
                throw new IOException("The change failed. Previous boot settings were restored: " + error.Message, error);
            }
        }
        catch (Exception error) when (error is ArgumentException or IOException or InvalidOperationException or UnauthorizedAccessException or ManagementException or COMException or Win32Exception or JsonException)
        { return new() { Success = false, Message = error.Message, Snapshot = Inspect() }; }
    }

    private BootSnapshot Inspect(BcdStore? existing = null)
    {
        BcdStore? own = null;
        List<string> errors = [];
        try
        {
            var bcd = existing ?? (own = new BcdStore());
            var records = bcd.Enumerate(); var manager = bcd.Read(Bcd.Manager); var current = bcd.Read(Bcd.Current).Id;
            var order = manager.Ids(Bcd.Order); var defaultId = manager.Text(Bcd.Default);
            if (defaultId.Length != 0) defaultId = Bcd.Id(defaultId);
            var menu = new BootMenuSettings { DefaultId = defaultId, Timeout = checked((int)manager.Number(Bcd.Timeout, 30)),
                DisplayMenu = manager.Flag(Bcd.DisplayMenu, true), Order = order, BootOnceId = manager.Ids(Bcd.Sequence).FirstOrDefault() ?? "" };
            var entries = records.Where(r => r.Type == Bcd.WindowsLoader || (r.Type & 0xf0000000) == 0x10000000 && (r.Type & 0x000fffff) is 6 or 7 or 8 || order.Contains(r.Id, StringComparer.OrdinalIgnoreCase))
                .Select(r => new BootEntry { Id = r.Id, Description = r.Text(Bcd.Description), Device = r.Device?.Path ?? "",
                    Path = r.Text(Bcd.Path), SystemRoot = r.Text(Bcd.SystemRoot), IsCurrent = r.Id == current,
                    IsDefault = r.Id == defaultId, IsWindows = r.Type == Bcd.WindowsLoader, Options = ReadOptions(r) })
                .OrderBy(e => order.IndexOf(e.Id) is var position && position >= 0 ? position : int.MaxValue).ThenBy(e => e.Id).ToList();
            if (!entries.Any(e => e.IsCurrent && e.IsWindows)) errors.Add("The running Windows loader could not be resolved to the system BCD store.");
            if (order.Any(id => entries.All(e => e.Id != id))) errors.Add("The boot menu references a missing BCD entry.");
            FirmwareState firmware;
            try
            {
                if (!WindowsMachine.Uefi) firmware = new() { Reason = "This Windows installation uses legacy BIOS." };
                else
                {
                    var fw = bcd.Read(Bcd.Firmware); var fwOrder = fw.Ids(Bcd.Order);
                    var fwEntries = records.Where(r => r.Id != Bcd.Firmware && ((r.Type & 0xfff00000) == 0x10100000 || fwOrder.Contains(r.Id)))
                        .Select(r => new FirmwareEntry { Id = r.Id, Description = r.Text(Bcd.Description), Device = r.Device?.Path ?? "",
                            Path = r.Text(Bcd.Path), IsWindowsBootManager = r.Id == Bcd.Manager }).ToList();
                    if (fwOrder.Any(id => fwEntries.All(e => e.Id != id))) throw new IOException("Firmware order references an unreadable entry.");
                    var currentFirmware = ResolveCurrentFirmware(fwEntries);
                    firmware = new() { Available = true, Entries = fwEntries.Select(e => e with { IsCurrent = e.Id == currentFirmware }).ToList(), Order = fwOrder,
                        BootOnceId = fw.Ids(Bcd.Sequence).FirstOrDefault() ?? "", CurrentId = currentFirmware };
                }
            }
            catch (Exception error) when (error is IOException or ManagementException or InvalidOperationException)
            { firmware = new() { Available = false, Reason = error.Message }; }
            var revision = Revision(records, current);
            List<BackupInfo> saved;
            try { saved = backups.List(); }
            catch (Exception error) when (error is IOException or UnauthorizedAccessException) { saved = []; errors.Add("Protected backups: " + error.Message); }
            var bitLocker = WindowsMachine.BitLocker();
            return new() { Revision = revision, RequiresAdministrator = !WindowsMachine.Administrator, Machine = Environment.MachineName, BitLockerProtectionEnabled = bitLocker.Protection == 1,
                BitLockerStatusKnown = bitLocker.Protection is 0 or 1,
                WindowsVersion = Environment.OSVersion.VersionString, Entries = entries, Menu = menu, Firmware = firmware,
                Backups = saved, Diagnostics = WindowsMachine.Diagnostics(), Repair = WindowsMachine.RepairPlan(), Errors = errors };
        }
        catch (Exception error) when (error is ManagementException or InvalidOperationException or IOException or UnauthorizedAccessException or COMException or ArgumentException or OverflowException)
        {
            return new() { RequiresAdministrator = !WindowsMachine.Administrator, Machine = Environment.MachineName,
                WindowsVersion = Environment.OSVersion.VersionString, Errors = [error.Message], Diagnostics = WindowsMachine.Diagnostics(),
                Repair = new() { Reason = "Boot configuration could not be inspected." } };
        }
        finally { own?.Dispose(); }
    }

    internal static void ValidateEntryDeletion(IEnumerable<BcdRecord> records, string targetId)
    {
        var target = Bcd.Id(targetId);
        // Only display-order references are removed by DeleteEntry. Other manager
        // references, including every item in a multi-entry boot sequence, must survive.
        var referenced = records.Where(record => record.Id != target).Any(record => record.Elements.Any(element =>
            !(record.Id == Bcd.Manager && element.Key == Bcd.Order) &&
            (((element.Key >> 24) & 0x0f) == 3 && string.Equals(element.Value as string, target, StringComparison.OrdinalIgnoreCase) ||
             ((element.Key >> 24) & 0x0f) == 4 && (element.Value as string[] ?? []).Contains(target, StringComparer.OrdinalIgnoreCase))));
        if (referenced) throw new ArgumentException("Another Windows recovery or boot object references this entry. Its registration cannot be deleted.");
    }

    internal static BootEntryOptions ReadOptions(BcdRecord entry) => new()
    {
        SafeBoot = !entry.Elements.ContainsKey(Bcd.SafeBoot) ? "Off" : entry.Flag(0x26000081) ? "AlternateShell" :
            entry.Number(Bcd.SafeBoot) switch { 1 => "Network", 2 => "DsRepair", _ => "Minimal" },
        BootLog = entry.Flag(Bcd.BootLog), Sos = entry.Flag(Bcd.Sos), BaseVideo = entry.Flag(Bcd.BaseVideo),
        RecoveryEnabled = entry.Flag(Bcd.Recovery, true), MenuPolicy = entry.Number(Bcd.MenuPolicy, 1) == 0 ? "Legacy" : "Standard"
    };
    private static string ResolveCurrentFirmware(List<FirmwareEntry> entries)
    {
        try
        {
            var current = FirmwareCurrent.Read(); var partitions = WindowsMachine.Partitions();
            var devices = partitions.Where(p => p.PartitionGuid.Equals(current.PartitionGuid, StringComparison.OrdinalIgnoreCase)).Select(p => p.Device).ToHashSet(StringComparer.OrdinalIgnoreCase);
            var matches = entries.Where(e => devices.Contains(e.Device) && e.Path.Equals(current.LoaderPath, StringComparison.OrdinalIgnoreCase) && e.Description == current.Description).ToList();
            return matches.Count == 1 ? matches[0].Id : "";
        }
        catch (Exception error) when (error is IOException or Win32Exception or ManagementException or UnauthorizedAccessException or DecoderFallbackException)
        { return ""; }
    }
    private static string Revision(List<BcdRecord> records, string current)
    {
        var normalized = records.OrderBy(r => r.Id).Select(r => new { r.Id, r.Type, Elements = r.RawElements?.OrderBy(e => e.Key).ToArray(), Known = r.RawElements is null ? r.Elements.OrderBy(e => e.Key).ToArray() : null });
        return Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(JsonSerializer.Serialize(new { current, records = normalized }))));
    }
    internal static bool Verified(BootSnapshot state, bool menu = true) =>
        state.Errors.Count == 0 && state.Entries.Count(e => e.IsCurrent && e.IsWindows) == 1 &&
        (!menu || state.Menu.Order.Contains(state.Menu.DefaultId) && state.Menu.Order.Contains(state.Entries.Single(e => e.IsCurrent).Id)) &&
        state.Menu.Order.All(id => state.Entries.Any(e => e.Id == id));
    private static void Verify(BootSnapshot state, bool menu = true)
    {
        if (!Verified(state, menu)) throw new IOException("Windows boot configuration verification failed.");
    }
    private static void VerifyRequested(BootRequest request, BootSnapshot before, BootSnapshot after)
    {
        bool Same(IEnumerable<string> first, IEnumerable<string> second) => first.SequenceEqual(second, StringComparer.OrdinalIgnoreCase);
        bool valid = request.Operation switch
        {
            BootOperation.SetMenu => after.Menu.DefaultId == Bcd.Id(request.Menu!.DefaultId) && after.Menu.Timeout == request.Menu.Timeout &&
                after.Menu.DisplayMenu == request.Menu.DisplayMenu && Same(after.Menu.Order, request.Menu.Order.Select(Bcd.Id)) && after.Menu.BootOnceId == before.Menu.BootOnceId,
            BootOperation.SetEntryOptions => after.Entries.Single(e => e.Id == Bcd.Id(request.TargetId)).Options == request.Options,
            BootOperation.RenameEntry => after.Entries.Single(e => e.Id == Bcd.Id(request.TargetId)).Description == BootValidation.Description(request.Description),
            BootOperation.CopyEntry => after.Entries.Where(e => before.Entries.All(old => old.Id != e.Id)).ToList() is var added && added.Count == 1 &&
                added[0].Description == BootValidation.Description(request.Description) && after.Menu.Order.Contains(added[0].Id),
            BootOperation.DeleteEntry => after.Entries.All(e => e.Id != Bcd.Id(request.TargetId)) && !after.Menu.Order.Contains(Bcd.Id(request.TargetId)),
            BootOperation.SetBootOnce => after.Menu.BootOnceId == Bcd.Id(request.TargetId),
            BootOperation.ClearBootOnce => after.Menu.BootOnceId.Length == 0,
            BootOperation.SetFirmwareOrder => after.Firmware.Available && Same(after.Firmware.Order, request.Order.Select(Bcd.Id)),
            BootOperation.SetFirmwareBootOnce => after.Firmware.BootOnceId == Bcd.Id(request.TargetId),
            BootOperation.ClearFirmwareBootOnce => after.Firmware.BootOnceId.Length == 0,
            BootOperation.AddFirmwareEntry => after.Firmware.Entries.Where(e => before.Firmware.Entries.All(old => old.Id != e.Id)).ToList() is var added && added.Count == 1 &&
                added[0].Description == BootValidation.Description(request.Description) && added[0].Path.Equals(request.EfiLoaderPath, StringComparison.OrdinalIgnoreCase) && after.Firmware.Order.Contains(added[0].Id),
            BootOperation.DeleteFirmwareEntry => after.Firmware.Entries.All(e => e.Id != Bcd.Id(request.TargetId)) && !after.Firmware.Order.Contains(Bcd.Id(request.TargetId)),
            BootOperation.ImportConfiguration => CheckConfiguration(BootConfiguration.Parse(request.ConfigurationJson, before), after) && after.Menu.BootOnceId == before.Menu.BootOnceId,
            BootOperation.Repair => after.Menu.DefaultId == before.Menu.DefaultId && after.Menu.Timeout == before.Menu.Timeout &&
                after.Menu.DisplayMenu == before.Menu.DisplayMenu && Same(after.Menu.Order, before.Menu.Order) && after.Menu.BootOnceId == before.Menu.BootOnceId && Same(after.Firmware.Order, before.Firmware.Order),
            _ => true
        };
        if (!valid) throw new IOException("Windows did not retain the requested boot settings. The change will be rolled back.");
    }
    private static bool CheckConfiguration(WindowsConfiguration configuration, BootSnapshot state) =>
        state.Menu.DefaultId.Equals(configuration.Menu.DefaultId, StringComparison.OrdinalIgnoreCase) && state.Menu.Timeout == configuration.Menu.Timeout &&
        state.Menu.DisplayMenu == configuration.Menu.DisplayMenu && state.Menu.Order.SequenceEqual(configuration.Menu.Order, StringComparer.OrdinalIgnoreCase) &&
        configuration.Entries.All(entry => state.Entries.Any(e => e.Id.Equals(entry.Id, StringComparison.OrdinalIgnoreCase) && e.Description == entry.Description && e.Options == entry.Options));
    private static void SetMenu(BcdStore bcd, BootMenuSettings menu)
    {
        bcd.Objects(Bcd.Manager, Bcd.Order, menu.Order); bcd.Object(Bcd.Manager, Bcd.Default, menu.DefaultId);
        bcd.Integer(Bcd.Manager, Bcd.Timeout, (ulong)menu.Timeout); bcd.Boolean(Bcd.Manager, Bcd.DisplayMenu, menu.DisplayMenu);
    }
    private static void SetOptions(BcdStore bcd, string id, BootEntryOptions options)
    {
        BootValidation.ValidateOptions(options);
        if (options.SafeBoot == "Off") { bcd.DeleteElement(id, Bcd.SafeBoot); bcd.DeleteElement(id, 0x26000081); }
        else { bcd.Integer(id, Bcd.SafeBoot, SafeBootValue(options.SafeBoot)); bcd.Boolean(id, 0x26000081, options.SafeBoot == "AlternateShell"); }
        bcd.Boolean(id, Bcd.BootLog, options.BootLog); bcd.Boolean(id, Bcd.Sos, options.Sos); bcd.Boolean(id, Bcd.BaseVideo, options.BaseVideo);
        bcd.Boolean(id, Bcd.Recovery, options.RecoveryEnabled); bcd.Integer(id, Bcd.MenuPolicy, options.MenuPolicy == "Legacy" ? 0ul : 1ul);
    }

    internal static ulong SafeBootValue(string safeBoot) => safeBoot switch { "Network" => 1ul, "DsRepair" => 2ul, _ => 0ul };

    private void Change(BcdStore bcd, BootRequest request, BootSnapshot before, EspPartition? esp, FirmwareImage? created, BackupManifest? restore, WindowsConfiguration? configuration, List<string> recreated)
    {
        switch (request.Operation)
        {
            case BootOperation.SetMenu: SetMenu(bcd, request.Menu!); break;
            case BootOperation.SetEntryOptions: SetOptions(bcd, request.TargetId, request.Options!); break;
            case BootOperation.RenameEntry: bcd.String(request.TargetId, Bcd.Description, BootValidation.Description(request.Description)); break;
            case BootOperation.CopyEntry:
                var copied = bcd.Copy(request.TargetId); bcd.String(copied, Bcd.Description, BootValidation.Description(request.Description));
                bcd.Objects(Bcd.Manager, Bcd.Order, before.Menu.Order.Append(copied)); break;
            case BootOperation.DeleteEntry:
                bcd.Objects(Bcd.Manager, Bcd.Order, before.Menu.Order.Where(id => id != Bcd.Id(request.TargetId))); bcd.Delete(request.TargetId); break;
            case BootOperation.SetBootOnce: bcd.Objects(Bcd.Manager, Bcd.Sequence, [request.TargetId]); break;
            case BootOperation.ClearBootOnce: bcd.DeleteElement(Bcd.Manager, Bcd.Sequence); break;
            case BootOperation.SetFirmwareOrder: bcd.Objects(Bcd.Firmware, Bcd.Order, request.Order); break;
            case BootOperation.SetFirmwareBootOnce: bcd.Objects(Bcd.Firmware, Bcd.Sequence, [request.TargetId]); break;
            case BootOperation.ClearFirmwareBootOnce: bcd.DeleteElement(Bcd.Firmware, Bcd.Sequence); break;
            case BootOperation.AddFirmwareEntry:
                CreateFirmware(bcd, created!); bcd.Objects(Bcd.Firmware, Bcd.Order, before.Firmware.Order.Append(created!.Id)); break;
            case BootOperation.DeleteFirmwareEntry:
                bcd.Objects(Bcd.Firmware, Bcd.Order, before.Firmware.Order.Where(id => id != Bcd.Id(request.TargetId))); bcd.Delete(request.TargetId); break;
            case BootOperation.RestoreBackup: Restore(bcd, restore!, false, recreated); break;
            case BootOperation.ImportConfiguration:
                foreach (var entry in configuration!.Entries) { bcd.String(entry.Id, Bcd.Description, entry.Description); SetOptions(bcd, entry.Id, entry.Options); }
                SetMenu(bcd, configuration.Menu); break;
            case BootOperation.Repair:
                WindowsMachine.RunBcdBoot(esp!, before.Entries.Single(e => e.IsCurrent).Id);
                // BCDBoot may recreate loader values; merge the complete previously exported store back after copying boot files.
                var repairBackup = backups.Read(backups.PendingId());
                var firmwareOrder = before.Firmware.Order.ToList();
                backups.ImportBcd(bcd, repairBackup);
                RestoreFirmware(bcd, repairBackup, firmwareOrder);
                break;
            default: throw new ArgumentException("Unsupported boot operation.");
        }
    }

    private static void ValidateFirmwareRecreation(FirmwareImage image)
    {
        if (image.Type != Bcd.FirmwareApplication || image.Device is not { Type: 2, AdditionalOptions.Length: 0 } || image.Id == Bcd.Manager)
            throw new ArgumentException("Only local EFI application registrations with a restorable partition device may be removed.");
        if (image.PartitionVolume.Length == 0) throw new ArgumentException("A stable verified EFI volume identity is required to recreate this registration.");
        var esp = WindowsMachine.ValidateEsp(image.PartitionVolume); WindowsMachine.EfiPath(esp, image.Path);
    }
    private static void CreateFirmware(BcdStore bcd, FirmwareImage image)
    {
        ValidateFirmwareRecreation(image); bcd.Create(image.Id, image.Type); bcd.String(image.Id, Bcd.Description, image.Description);
        bcd.Partition(image.Id, WindowsMachine.ValidateEsp(image.PartitionVolume).Device); bcd.String(image.Id, Bcd.Path, image.Path);
    }
    private static bool SameFirmware(BcdRecord record, FirmwareImage image, bool partial = false) => record.Type == image.Type &&
        (record.Text(Bcd.Path).Equals(image.Path, StringComparison.OrdinalIgnoreCase) || partial && record.Text(Bcd.Path).Length == 0) &&
        ((image.PartitionVolume.Length == 0 ? record.Device == image.Device : record.Device is { Type: 2, AdditionalOptions.Length: 0 } &&
            record.Device.Path.Equals(WindowsMachine.ValidateEsp(image.PartitionVolume).Device, StringComparison.OrdinalIgnoreCase)) || partial && record.Device is null) &&
        (record.Text(Bcd.Description) == image.Description || partial && record.Text(Bcd.Description).Length == 0);

    private void ValidateRestore(BcdStore live, BackupManifest manifest, bool rollback)
    {
        using var archived = backups.OpenBcd(manifest);
        var saved = archived.Enumerate(); var current = live.Read(Bcd.Current).Id;
        if (manifest.CurrentId != current || !saved.Any(e => e.Id == current && e.Type == Bcd.WindowsLoader))
            throw new IOException("The backup does not contain the running Windows loader.");
        // Rollback must always put back the exact previous state, even a menu that lacked the default or running loader.
        if (!rollback)
        {
            var manager = archived.Read(Bcd.Manager); var order = manager.Ids(Bcd.Order); var defaultId = manager.Text(Bcd.Default);
            if (defaultId.Length != 0) defaultId = Bcd.Id(defaultId);
            if (!order.Contains(current) || !order.Contains(defaultId) || order.Any(id => !saved.Any(e => e.Id == id)))
                throw new IOException("The backed-up boot menu has a missing current or default loader.");
            WindowsMachine.ValidateWindowsLoader(saved.Single(e => e.Id == defaultId));
        }
        var records = live.Enumerate();
        if (!rollback) ValidateIntegrityRestore(saved, records);
        foreach (var image in manifest.Firmware)
        {
            var item = records.SingleOrDefault(r => r.Id == image.Id);
            if (item is null) ValidateFirmwareRecreation(image);
            else if (image.Id != Bcd.Manager && !SameFirmware(item, image))
                throw new IOException("A firmware registration changed identity since this backup. Reload and investigate before restoring.");
        }
        if (manifest.CreatedFirmwareId.Length != 0)
        {
            var item = records.SingleOrDefault(r => r.Id == manifest.CreatedFirmwareId);
            if (item is not null && (manifest.CreatedFirmware is null || !SameFirmware(item, manifest.CreatedFirmware, rollback)))
                throw new IOException("The created firmware identifier has been reused or changed; it cannot be removed by this backup.");
            if (item is not null)
            {
                var entries = records.Where(r => r.Id != Bcd.Firmware && (r.Type & 0xfff00000) == 0x10100000)
                    .Select(r => new FirmwareEntry { Id = r.Id, Description = r.Text(Bcd.Description), Device = r.Device?.Path ?? "", Path = r.Text(Bcd.Path) }).ToList();
                ValidateCreatedFirmwareUndo(manifest.CreatedFirmwareId, ResolveCurrentFirmware(entries), manifest.BootSession, WindowsMachine.BootSession());
            }
            if (live.Read(Bcd.Firmware).Ids(Bcd.Sequence).Contains(manifest.CreatedFirmwareId))
                throw new IOException("Clear the scheduled firmware boot before undoing its registration.");
        }
        if (manifest.Efi is not null) _ = WindowsMachine.ValidateEsp(manifest.Efi.Volume, true);
    }
    internal static void ValidateIntegrityRestore(IReadOnlyCollection<BcdRecord> saved, IReadOnlyCollection<BcdRecord> current)
    {
        // Preserve an existing baseline, including test/development machines, without
        // enabling a bypass that is absent now. These identifiers are documented BCD
        // library/OS-loader Boolean elements; the third is the legacy loader setting.
        uint[] protectedElements = [0x16000048, 0x16000049, 0x26000027, 0x260000e1];
        const uint inheritedObjects = 0x14000006;
        var archived = saved.ToDictionary(record => record.Id, StringComparer.OrdinalIgnoreCase);
        var live = current.ToDictionary(record => record.Id, StringComparer.OrdinalIgnoreCase);
        foreach (var type in protectedElements)
        {
            bool HasEnabled(BcdRecord record, HashSet<string> visited)
            {
                if (!visited.Add(record.Id)) return false;
                if (record.Elements.ContainsKey(type)) return record.Flag(type);
                return record.Ids(inheritedObjects).Any(id => archived.TryGetValue(id, out var parent) && HasEnabled(parent, visited));
            }
            void Refuse(BcdRecord record) => throw new IOException($"Restoring this backup would introduce or change protected integrity/signing setting 0x{type:x8} on {record.Id}. Uoo only preserves an already-existing baseline.");
            void PreserveInheritance(BcdRecord record, HashSet<string> visited)
            {
                if (!visited.Add(record.Id)) return;
                if (!live.TryGetValue(record.Id, out var existing) || existing.Type != record.Type ||
                    record.Elements.ContainsKey(type) != existing.Elements.ContainsKey(type) || record.Flag(type) != existing.Flag(type) ||
                    !record.Ids(inheritedObjects).SequenceEqual(existing.Ids(inheritedObjects), StringComparer.OrdinalIgnoreCase))
                    Refuse(record);
                foreach (var id in record.Ids(inheritedObjects))
                    if (archived.TryGetValue(id, out var parent)) PreserveInheritance(parent, visited);
            }
            foreach (var record in saved)
            {
                if (record.Flag(type) && (!live.TryGetValue(record.Id, out var existing) || existing.Type != record.Type || !existing.Flag(type)))
                    Refuse(record);
                // A restored inheritance edge must not newly expose another object to
                // a flag that already exists on a shared settings object. Compare the
                // security-relevant inheritance graph, including explicit false values.
                if (!record.Elements.ContainsKey(type) && HasEnabled(record, new(StringComparer.OrdinalIgnoreCase)))
                    PreserveInheritance(record, new(StringComparer.OrdinalIgnoreCase));
            }
        }
    }
    internal static void ValidateCreatedFirmwareUndo(string createdId, string currentFirmwareId, string savedBootSession, string currentBootSession)
    {
        if (currentFirmwareId.Length != 0)
        {
            if (currentFirmwareId.Equals(createdId, StringComparison.OrdinalIgnoreCase))
                throw new IOException("The created registration is now the current firmware boot entry and cannot be removed by restore.");
            return;
        }
        if (savedBootSession.Length == 0 || currentBootSession.Length == 0 || savedBootSession != currentBootSession)
            throw new IOException("The current firmware entry is unknown and the creation boot session cannot be proven. The registration will be preserved; recovery information remains available.");
        // A fresh registration did not exist at the start of this proven boot session, so
        // it cannot be BootCurrent even when the firmware provider cannot identify BootCurrent.
    }
    private static void DeleteFirmware(BcdStore bcd, string id)
    {
        bcd.Objects(Bcd.Firmware, Bcd.Sequence, bcd.Read(Bcd.Firmware).Ids(Bcd.Sequence).Where(other => other != id));
        bcd.Objects(Bcd.Firmware, Bcd.Order, bcd.Read(Bcd.Firmware).Ids(Bcd.Order).Where(other => other != id));
        bcd.Delete(id);
    }
    private void Restore(BcdStore bcd, BackupManifest manifest, bool rollback, List<string>? recreated = null)
    {
        ValidateRestore(bcd, manifest, rollback);
        var records = bcd.Enumerate();
        var previousFirmwareOrder = manifest.Firmware.Count == 0 ? [] : bcd.Read(Bcd.Firmware).Ids(Bcd.Order);
        foreach (var missing in manifest.Firmware.Where(image => records.All(r => r.Id != image.Id)))
        { recreated?.Add(missing.Id); CreateFirmware(bcd, missing); }
        if (manifest.CreatedFirmwareId.Length != 0 && records.Any(r => r.Id == manifest.CreatedFirmwareId))
            DeleteFirmware(bcd, manifest.CreatedFirmwareId);
        backups.RestoreTree(manifest); backups.ImportBcd(bcd, manifest);
        RestoreFirmware(bcd, manifest, previousFirmwareOrder);
        using var archived = backups.OpenBcd(manifest);
        var expected = archived.Enumerate(); var actual = bcd.Enumerate();
        // {bootmgr} also has a firmware-image object type on UEFI, but its menu/default elements must be checked.
        bool Firmware(BcdRecord record) => record.Id != Bcd.Manager && (record.Id == Bcd.Firmware || (record.Type & 0xfff00000) == 0x10100000);
        var oldBcd = expected.Where(r => !Firmware(r)).OrderBy(r => r.Id).ToList();
        var newBcd = actual.Where(r => !Firmware(r)).OrderBy(r => r.Id).ToList();
        if (oldBcd.Count != newBcd.Count || oldBcd.Where((record, i) => record.Id != newBcd[i].Id || record.Type != newBcd[i].Type ||
            !record.RawElements!.OrderBy(e => e.Key).SequenceEqual(newBcd[i].RawElements!.OrderBy(e => e.Key))).Any())
            throw new IOException("Restored BCD elements did not match the protected backup.");
    }
    private static void RestoreFirmware(BcdStore bcd, BackupManifest manifest, List<string> previousOrder)
    {
        if (manifest.Firmware.Count == 0) return;
        // Existing registrations recover their saved inclusion; only genuinely later
        // external registrations retain their later place in BootOrder.
        var known = manifest.Firmware.Select(image => image.Id).ToHashSet(StringComparer.OrdinalIgnoreCase);
        var later = previousOrder.Where(id => id != manifest.CreatedFirmwareId && !known.Contains(id));
        var desired = manifest.FirmwareOrder.Concat(later).Distinct(StringComparer.OrdinalIgnoreCase).ToList();
        bcd.Objects(Bcd.Firmware, Bcd.Order, desired); bcd.Objects(Bcd.Firmware, Bcd.Sequence, manifest.FirmwareOnce);
        var firmware = bcd.Read(Bcd.Firmware);
        if (!firmware.Ids(Bcd.Order).SequenceEqual(desired, StringComparer.OrdinalIgnoreCase) ||
            !firmware.Ids(Bcd.Sequence).SequenceEqual(manifest.FirmwareOnce, StringComparer.OrdinalIgnoreCase))
            throw new IOException("Restored firmware state did not match the protected backup. Expected order " + string.Join(",", desired) +
                "; actual " + string.Join(",", firmware.Ids(Bcd.Order)) + "; expected once " + string.Join(",", manifest.FirmwareOnce) +
                "; actual " + string.Join(",", firmware.Ids(Bcd.Sequence)) + ".");
    }

}
