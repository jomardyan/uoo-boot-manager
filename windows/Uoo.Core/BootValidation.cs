using System.Text.RegularExpressions;

namespace Uoo.Core;

/// <summary>Shared, side-effect-free checks. The privileged provider rechecks live state and files.</summary>
public static partial class BootValidation
{
    public static bool RequiresRecoveryKey(BootSnapshot snapshot) => !snapshot.Demo && (snapshot.BitLockerProtectionEnabled || !snapshot.BitLockerStatusKnown);
    public static string Id(string value)
    {
        if (value is null || !Guid.TryParseExact(value, "B", out var guid) || guid == Guid.Empty)
            throw new ArgumentException("Choose a boot entry with a valid identifier.");
        return guid.ToString("B");
    }

    public static string Description(string value)
    {
        if (string.IsNullOrWhiteSpace(value) || value.Length > 120 || value.Any(char.IsControl))
            throw new ArgumentException("Enter a readable name of 1–120 characters without control characters.");
        return value.Trim();
    }

    public static void ValidateOptions(BootEntryOptions options)
    {
        ArgumentNullException.ThrowIfNull(options);
        if (options.SafeBoot is not ("Off" or "Minimal" or "Network" or "AlternateShell"))
            throw new ArgumentException("Safe mode must be Off, Minimal, Network, or AlternateShell.");
        if (options.MenuPolicy is not ("Standard" or "Legacy"))
            throw new ArgumentException("Boot menu style must be Standard or Legacy.");
    }

    public static void ValidateEfiLoaderPath(string loader)
    {
        if (loader is null || loader.Length is < 10 or > 240 || !EfiPath().IsMatch(loader) ||
            loader.Split('\\').Skip(1).Any(p => p.Length == 0 || p is "." or ".." || p.EndsWith(' ') || p.EndsWith('.')))
            throw new ArgumentException(@"Choose an existing EFI application under \EFI with a .efi extension.");
    }

    public static void ValidateMenu(BootMenuSettings menu, BootSnapshot snapshot)
    {
        ArgumentNullException.ThrowIfNull(menu);
        if (menu.Timeout is < 0 or > 300)
            throw new ArgumentException("The boot menu timeout must be 0–300 seconds.");
        var allowed = snapshot.Entries.Select(x => x.Id).ToHashSet(StringComparer.OrdinalIgnoreCase);
        ValidateOrder(menu.Order, allowed);
        Id(menu.DefaultId);
        if (!menu.Order.Contains(menu.DefaultId, StringComparer.OrdinalIgnoreCase))
            throw new ArgumentException("The default entry must be included in the boot menu.");
        foreach (var current in snapshot.Entries.Where(x => x.IsCurrent))
            if (!menu.Order.Contains(current.Id, StringComparer.OrdinalIgnoreCase))
                throw new ArgumentException("Keep the running Windows installation in the boot menu.");
    }

    public static void ValidateOrder(IReadOnlyList<string> order, ISet<string> allowed)
    {
        if (order is null || order.Count is < 1 or > 128 || order.Distinct(StringComparer.OrdinalIgnoreCase).Count() != order.Count)
            throw new ArgumentException("Choose a nonempty boot order without duplicate entries.");
        foreach (var id in order)
            if (!allowed.Contains(Id(id))) throw new ArgumentException("The boot order contains an entry that is no longer available. Reload first.");
    }

    public static void Validate(BootRequest request, BootSnapshot snapshot)
    {
        ArgumentNullException.ThrowIfNull(request);
        ArgumentNullException.ThrowIfNull(snapshot);
        if (!Enum.IsDefined(request.Operation)) throw new ArgumentException("Unsupported boot operation.");
        if (request.Operation != BootOperation.Inspect && string.IsNullOrEmpty(request.ExpectedRevision))
            throw new ArgumentException("Reload boot settings before making changes.");
        if (request.Operation != BootOperation.Inspect && request.ExpectedRevision != snapshot.Revision)
            throw new ArgumentException("Boot settings have changed. Reload and review them before applying this change.");
        if (request.Operation is BootOperation.SetMenu)
            ValidateMenu(request.Menu ?? throw new ArgumentException("Choose boot menu settings."), snapshot);
        if (request.Operation == BootOperation.CreateBackup && !string.IsNullOrWhiteSpace(request.Description))
            Description(request.Description);
        if (request.Operation is BootOperation.RenameEntry or BootOperation.CopyEntry or BootOperation.AddFirmwareEntry)
            Description(request.Description);
        if (request.Operation is BootOperation.SetEntryOptions or BootOperation.RenameEntry or BootOperation.CopyEntry or BootOperation.DeleteEntry or BootOperation.SetBootOnce)
        {
            var entry = snapshot.Entries.SingleOrDefault(x => string.Equals(x.Id, Id(request.TargetId), StringComparison.OrdinalIgnoreCase))
                ?? throw new ArgumentException("The selected boot entry is no longer available.");
            if (!entry.IsWindows && request.Operation != BootOperation.SetBootOnce) throw new ArgumentException("Choose a Windows operating-system entry.");
            if (request.Operation is BootOperation.DeleteEntry &&
                (entry.IsCurrent || entry.IsDefault || snapshot.Menu.BootOnceId == entry.Id || snapshot.Entries.Count(x => x.IsWindows) <= 1))
                throw new ArgumentException("The current, default, scheduled, or last Windows boot entry cannot be deleted.");
        }
        if (request.Operation is BootOperation.SetEntryOptions)
            ValidateOptions(request.Options ?? throw new ArgumentException("Choose entry options."));
        if (request.Operation is BootOperation.SetFirmwareOrder or BootOperation.SetFirmwareBootOnce or BootOperation.ClearFirmwareBootOnce or BootOperation.AddFirmwareEntry or BootOperation.DeleteFirmwareEntry)
        {
            if (!snapshot.Firmware.Available) throw new ArgumentException(snapshot.Firmware.Reason.Length == 0 ? "UEFI firmware is unavailable." : snapshot.Firmware.Reason);
        }
        if (request.Operation is BootOperation.SetFirmwareOrder)
        {
            var allowed = snapshot.Firmware.Entries.Select(x => x.Id).ToHashSet(StringComparer.OrdinalIgnoreCase);
            ValidateOrder(request.Order, allowed);
            if (string.IsNullOrWhiteSpace(snapshot.Firmware.CurrentId) &&
                snapshot.Firmware.Order.Except(request.Order, StringComparer.OrdinalIgnoreCase).Any())
                throw new ArgumentException("The current firmware entry could not be identified. Keep every existing order entry; reordering and additions are still available.");
            // Inclusion is explicit in the UI; excluding an entry never deletes its registration.
            foreach (var protectedId in new[] { snapshot.Firmware.CurrentId, snapshot.Firmware.BootOnceId })
                if (!string.IsNullOrWhiteSpace(protectedId) && snapshot.Firmware.Order.Contains(protectedId, StringComparer.OrdinalIgnoreCase) &&
                    !request.Order.Contains(protectedId, StringComparer.OrdinalIgnoreCase))
                    throw new ArgumentException("Keep the current and scheduled firmware entries in the boot order.");
        }
        if (request.Operation is BootOperation.SetFirmwareBootOnce or BootOperation.DeleteFirmwareEntry)
        {
            var entry = snapshot.Firmware.Entries.SingleOrDefault(x => string.Equals(x.Id, Id(request.TargetId), StringComparison.OrdinalIgnoreCase))
                ?? throw new ArgumentException("The selected firmware entry is no longer available.");
            if (request.Operation == BootOperation.DeleteFirmwareEntry &&
                (entry.IsWindowsBootManager || entry.IsCurrent || snapshot.Firmware.CurrentId == entry.Id || snapshot.Firmware.Entries.Count <= 1 || snapshot.Firmware.BootOnceId == entry.Id))
                throw new ArgumentException("Windows Boot Manager, a scheduled entry, or the last firmware entry cannot be removed.");
        }
        if (request.Operation is BootOperation.AddFirmwareEntry)
        {
            ValidateEfiLoaderPath(request.EfiLoaderPath);
            if (!DriveRoot().IsMatch(request.EfiPartition ?? "") && !VolumeRoot().IsMatch(request.EfiPartition ?? ""))
                throw new ArgumentException(@"Choose a local EFI system partition, such as S:\ or its volume GUID path.");
        }
        if (request.Operation is BootOperation.RestoreBackup or BootOperation.DeleteBackup)
        {
            if (!BackupId().IsMatch(request.BackupId ?? "")) throw new ArgumentException("Choose a valid backup.");
            var backup = snapshot.Backups.SingleOrDefault(x => x.Id == request.BackupId)
                ?? throw new ArgumentException("The selected backup is no longer available.");
            if (request.Operation == BootOperation.RestoreBackup && !backup.CanRestore)
                throw new ArgumentException("This backup cannot be restored: " + backup.Detail);
        }
        if (request.Operation is BootOperation.ImportConfiguration or BootOperation.ExportConfiguration)
        {
            if (request.FilePath is null || request.FilePath.Length > 1024 || request.FilePath.Any(char.IsControl))
                throw new ArgumentException("Choose a configuration JSON file.");
            if (request.ConfigurationJson is null || System.Text.Encoding.UTF8.GetByteCount(request.ConfigurationJson) > BootConfiguration.MaximumBytes)
                throw new ArgumentException("Configuration files must be smaller than 1 MiB.");
            if (request.Operation == BootOperation.ImportConfiguration && string.IsNullOrWhiteSpace(request.FilePath) && string.IsNullOrWhiteSpace(request.ConfigurationJson))
                throw new ArgumentException("Choose a Windows configuration JSON file.");
        }
        if (request.Operation is BootOperation.Repair)
        {
            if (!snapshot.Repair.Available) throw new ArgumentException(snapshot.Repair.Reason);
            if (request.Confirmation != snapshot.Repair.Confirmation)
                throw new ArgumentException("Type the repair confirmation exactly as shown in the reviewed plan.");
        }
    }

    [GeneratedRegex(@"^\\EFI\\[A-Za-z0-9 _().+\\-]+\.efi\z", RegexOptions.IgnoreCase | RegexOptions.CultureInvariant)]
    private static partial Regex EfiPath();
    [GeneratedRegex(@"^[A-Za-z]:\\\z")]
    private static partial Regex DriveRoot();
    [GeneratedRegex(@"^\\\\\?\\Volume\{[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\}\\\z")]
    private static partial Regex VolumeRoot();
    [GeneratedRegex(@"^[A-Za-z0-9][A-Za-z0-9_-]{0,95}\z")]
    private static partial Regex BackupId();
}
