namespace Uoo.Core;

public enum BootOperation
{
    Inspect, SetMenu, SetEntryOptions, RenameEntry, CopyEntry, DeleteEntry,
    SetBootOnce, ClearBootOnce, SetFirmwareOrder, SetFirmwareBootOnce,
    ClearFirmwareBootOnce, AddFirmwareEntry, DeleteFirmwareEntry,
    CreateBackup, RestoreBackup, DeleteBackup, ExportConfiguration,
    ImportConfiguration, Repair
}

public sealed record BootEntry
{
    public string Id { get; init; } = "";
    public string Description { get; init; } = "";
    public string Device { get; init; } = "";
    public string Path { get; init; } = "";
    public string SystemRoot { get; init; } = "";
    public bool IsCurrent { get; init; }
    public bool IsDefault { get; init; }
    public bool IsWindows { get; init; }
    public BootEntryOptions Options { get; init; } = new();
    public override string ToString() => Description;
}

public sealed record BootEntryOptions
{
    public string SafeBoot { get; init; } = "Off";
    public bool BootLog { get; init; }
    public bool Sos { get; init; }
    public bool BaseVideo { get; init; }
    public bool RecoveryEnabled { get; init; } = true;
    public string MenuPolicy { get; init; } = "Standard";
}

public sealed record BootMenuSettings
{
    public string DefaultId { get; init; } = "";
    public int Timeout { get; init; } = 30;
    public bool DisplayMenu { get; init; } = true;
    public List<string> Order { get; init; } = [];
    public string BootOnceId { get; init; } = "";
}

public sealed record FirmwareEntry
{
    public string Id { get; init; } = "";
    public string Description { get; init; } = "";
    public string Device { get; init; } = "";
    public string Path { get; init; } = "";
    public bool IsWindowsBootManager { get; init; }
    public bool IsCurrent { get; init; }
    public override string ToString() => Description;
}

public sealed record FirmwareState
{
    public bool Available { get; init; }
    public string Reason { get; init; } = "";
    public List<FirmwareEntry> Entries { get; init; } = [];
    public List<string> Order { get; init; } = [];
    public string BootOnceId { get; init; } = "";
    public string CurrentId { get; init; } = "";
}

public sealed record BackupInfo
{
    public string Id { get; init; } = "";
    public string Description { get; init; } = "";
    public DateTimeOffset CreatedAt { get; init; }
    public bool CanRestore { get; init; }
    public string Detail { get; init; } = "";
    public override string ToString() => $"{CreatedAt.LocalDateTime:g} — {Description}";
}

public sealed record DiagnosticItem(string Name, string Status, string Detail);

public sealed record RepairPlan
{
    public bool Available { get; init; }
    public string Reason { get; init; } = "";
    public string WindowsDirectory { get; init; } = "";
    public string SystemPartition { get; init; } = "";
    public string Description { get; init; } = "";
    public string Confirmation { get; init; } = "REPAIR";
}

/// <summary>Free space on a verified local EFI System Partition.</summary>
public sealed record EspSpace(string Partition, long FreeBytes, long TotalBytes);

/// <summary>Windows settings that commonly cause dual-boot trouble. A null value means it could not be read.</summary>
public sealed record DualBootFacts
{
    public bool? FastStartupEnabled { get; init; }
    public bool? ClockIsUtc { get; init; }
    public bool? SecureBootEnabled { get; init; }
    public List<EspSpace> Esps { get; init; } = [];
}

public sealed record BootSnapshot
{
    public bool Demo { get; init; }
    public bool RequiresAdministrator { get; init; }
    public bool BitLockerProtectionEnabled { get; init; }
    public bool BitLockerStatusKnown { get; init; } = true;
    public string Revision { get; init; } = "";
    public string Machine { get; init; } = "";
    public string WindowsVersion { get; init; } = "";
    public List<BootEntry> Entries { get; init; } = [];
    public BootMenuSettings Menu { get; init; } = new();
    public FirmwareState Firmware { get; init; } = new();
    public List<BackupInfo> Backups { get; init; } = [];
    public List<DiagnosticItem> Diagnostics { get; init; } = [];
    public RepairPlan Repair { get; init; } = new();
    public List<LinuxLoader> LinuxLoaders { get; init; } = [];
    public DualBootFacts DualBootFacts { get; init; } = new();
    public List<string> Errors { get; init; } = [];
}

public sealed record BootRequest
{
    public BootOperation Operation { get; init; }
    public string ExpectedRevision { get; init; } = "";
    public string TargetId { get; init; } = "";
    public string Description { get; init; } = "";
    public List<string> Order { get; init; } = [];
    public BootMenuSettings? Menu { get; init; }
    public BootEntryOptions? Options { get; init; }
    public string BackupId { get; init; } = "";
    public string FilePath { get; init; } = "";
    public string EfiLoaderPath { get; init; } = "";
    public string EfiPartition { get; init; } = "";
    public string Confirmation { get; init; } = "";
    public bool RecoveryKeyAcknowledged { get; init; }
    public string ConfigurationJson { get; init; } = "";
}

public sealed record BootResponse
{
    public bool Success { get; init; }
    public string Message { get; init; } = "";
    public BootSnapshot? Snapshot { get; init; }
    public string ConfigurationJson { get; init; } = "";
}

public sealed record WindowsConfiguration
{
    public string Format { get; init; } = "uoo-windows-configuration";
    public int Version { get; init; } = 1;
    public BootMenuSettings Menu { get; init; } = new();
    public List<ConfigurationEntry> Entries { get; init; } = [];
}

public sealed record ConfigurationEntry
{
    public string Id { get; init; } = "";
    public string Description { get; init; } = "";
    public BootEntryOptions Options { get; init; } = new();
}

public interface IBootService
{
    Task<BootResponse> ExecuteAsync(BootRequest request, CancellationToken cancellationToken = default);
}
