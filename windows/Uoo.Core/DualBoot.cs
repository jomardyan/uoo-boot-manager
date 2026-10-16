namespace Uoo.Core;

public enum DualBootAction { RegisterLoader, IncludeInOrder, StartFirst }

/// <summary>A Linux EFI loader found on a verified local system partition.</summary>
public sealed record LinuxLoader(string Distribution, string Partition, string Path);

public sealed record DualBootFinding(string Id, string Severity, string Title, string Detail, string StepId = "");

public sealed record DualBootStep(string Id, DualBootAction Action, string Target, string Title, string Detail, bool Recommended);

public sealed record DualBootPlan
{
    public bool Supported { get; init; }
    public string Reason { get; init; } = "";
    public List<DualBootFinding> Findings { get; init; } = [];
    public List<DualBootStep> Steps { get; init; } = [];
    public bool Encrypted { get; init; }
}

/// <summary>Well-known Linux EFI directories. Detection never guesses beyond this list.</summary>
public static class LinuxDistributions
{
    private static readonly string[] GrubFiles = ["shimx64.efi", "shimaa64.efi", "grubx64.efi", "grubaa64.efi"];
    public static readonly IReadOnlyList<(string Directory, string Name, string[] Files)> Known =
    [
        ("ubuntu", "Ubuntu", GrubFiles), ("debian", "Debian", GrubFiles), ("fedora", "Fedora", GrubFiles),
        ("opensuse", "openSUSE", ["shim.efi", "grub.efi"]), ("arch", "Arch Linux", ["grubx64.efi"]),
        ("manjaro", "Manjaro", ["grubx64.efi"]), ("endeavouros", "EndeavourOS", ["grubx64.efi"]),
        ("centos", "CentOS", GrubFiles), ("rocky", "Rocky Linux", GrubFiles), ("almalinux", "AlmaLinux", GrubFiles),
        ("redhat", "Red Hat Enterprise Linux", GrubFiles), ("kali", "Kali Linux", GrubFiles)
    ];

    /// <summary>The distribution name for an <c>\EFI\name\file.efi</c> path, or null for Windows and unknown loaders.</summary>
    public static string? Identify(string efiPath)
    {
        var parts = (efiPath ?? "").Split('\\', StringSplitOptions.RemoveEmptyEntries);
        if (parts.Length < 3 || !parts[0].Equals("EFI", StringComparison.OrdinalIgnoreCase)) return null;
        foreach (var known in Known)
            if (known.Directory.Equals(parts[1], StringComparison.OrdinalIgnoreCase)) return known.Name;
        return null;
    }
}

/// <summary>
/// Pure dual-boot analysis for the Windows edition. Every step is expressed as an existing, validated
/// <see cref="BootRequest"/>; nothing here touches encryption, Secure Boot, or BitLocker protectors.
/// </summary>
public static class DualBoot
{
    /// <summary>A Windows-created ESP is often 100 MiB; below this, boot file updates start to fail.</summary>
    public const long LowEspBytes = 8L * 1024 * 1024;

    public static DualBootPlan Analyze(BootSnapshot snapshot)
    {
        ArgumentNullException.ThrowIfNull(snapshot);
        if (snapshot.RequiresAdministrator)
            return new() { Reason = "Load the boot settings as administrator for a complete dual-boot check." };
        if (!snapshot.Firmware.Available)
            return new() { Reason = snapshot.Firmware.Reason.Length > 0 ? snapshot.Firmware.Reason : "Dual-boot checks need UEFI firmware." };

        List<DualBootFinding> findings = [];
        List<DualBootStep> steps = [];
        var encrypted = snapshot.BitLockerProtectionEnabled || !snapshot.BitLockerStatusKnown;
        var order = snapshot.Firmware.Order;
        var linuxEntries = snapshot.Firmware.Entries.Where(e => LinuxDistributions.Identify(e.Path) is not null).ToList();
        var evidence = linuxEntries.Count > 0 || snapshot.LinuxLoaders.Count > 0;

        foreach (var loader in snapshot.LinuxLoaders.GroupBy(l => l.Distribution).Select(g => g.First()))
        {
            if (snapshot.Firmware.Entries.Any(e => e.Path.Equals(loader.Path, StringComparison.OrdinalIgnoreCase))) continue;
            var step = new DualBootStep("register:" + loader.Path.ToLowerInvariant(), DualBootAction.RegisterLoader, loader.Path,
                $"Register {loader.Distribution} in the UEFI firmware",
                $"Add a firmware entry for {loader.Path}. It goes after the existing entries, so the boot order does not change.", true);
            steps.Add(step);
            findings.Add(new("linux_not_registered", "problem", $"{loader.Distribution} is installed but not in the UEFI boot list",
                $"Windows found {loader.Path} on the system partition, but the firmware has no entry for it. This often happens after a firmware reset or update.", step.Id));
        }

        var firstLinux = linuxEntries.Where(e => order.Contains(e.Id, StringComparer.OrdinalIgnoreCase))
            .OrderBy(e => order.FindIndex(id => id.Equals(e.Id, StringComparison.OrdinalIgnoreCase))).FirstOrDefault();
        foreach (var entry in linuxEntries.Where(e => !order.Contains(e.Id, StringComparer.OrdinalIgnoreCase)))
        {
            var step = new DualBootStep("include:" + entry.Id, DualBootAction.IncludeInOrder, entry.Id,
                $"Add {entry.Description} back to the boot order", "Append the entry after the existing ones. The order of the other entries does not change.", true);
            steps.Add(step);
            findings.Add(new("linux_excluded", "warning", $"{entry.Description} is not in the UEFI boot order",
                "The entry is registered but the firmware will not try it automatically.", step.Id));
        }
        var windowsIndex = snapshot.Firmware.Entries.Where(e => e.IsWindowsBootManager)
            .Select(e => order.FindIndex(id => id.Equals(e.Id, StringComparison.OrdinalIgnoreCase))).Where(i => i >= 0).DefaultIfEmpty(-1).Min();
        if (firstLinux is not null && windowsIndex >= 0 &&
            windowsIndex < order.FindIndex(id => id.Equals(firstLinux.Id, StringComparison.OrdinalIgnoreCase)))
        {
            var caution = encrypted ? " BitLocker is on: booting Windows through the Linux menu can change what the TPM measures and may ask for the recovery key." : "";
            var step = new DualBootStep("first:" + firstLinux.Id, DualBootAction.StartFirst, firstLinux.Id,
                $"Start {firstLinux.Description}'s boot menu first",
                "Move it above Windows Boot Manager so its menu appears at startup. You can reorder later on the Firmware page." + caution, false);
            steps.Add(step);
            findings.Add(new("windows_boots_first", "info", "Windows starts before the Linux boot menu",
                "Windows Boot Manager is first, so the Linux menu does not appear unless you pick it in the firmware boot menu. This is fine if you want Windows by default; the step is optional.", step.Id));
        }

        var facts = snapshot.DualBootFacts ?? new();
        if (facts.Esps.Where(e => e.FreeBytes < LowEspBytes).OrderBy(e => e.FreeBytes).FirstOrDefault() is { } low)
            findings.Add(new("esp_nearly_full", "warning", "The EFI System Partition is nearly full",
                $"Only about {Math.Max(0, low.FreeBytes) / (1024 * 1024)} MiB is free. A full partition makes boot file updates fail for both systems. Remove only vendor folders you are sure are obsolete, or enlarge the partition with a backup first. Uoo does not resize partitions."));
        if (!snapshot.Repair.Available && snapshot.LinuxLoaders.Count == 0 && facts.Esps.Count == 0)
            findings.Add(new("esp_unverified", "info", "Linux loaders could not be scanned",
                snapshot.Repair.Reason.Length > 0 ? snapshot.Repair.Reason : "Uoo could not verify a local GPT/FAT32 EFI System Partition to scan."));
        if (!evidence)
            findings.Add(new("no_linux", "info", "No Linux installation was detected",
                "Uoo found no Linux EFI loader on the system partition and no Linux entry in the firmware. If Linux uses another disk's EFI partition, use Firmware, then Add an EFI loader."));
        if (evidence && encrypted)
            findings.Add(new("bitlocker", "warning", snapshot.BitLockerStatusKnown ? "BitLocker protection is on" : "BitLocker status could not be read",
                "Windows 11 can turn BitLocker on by itself (device encryption). Keep the 48-digit recovery password on another device. Changing the boot order, the Secure Boot state, or starting Windows through the Linux boot menu can make Windows ask for it. Uoo never suspends or changes BitLocker."));
        if (evidence)
        {
            if (facts.FastStartupEnabled == true)
                findings.Add(new("fast_startup", "warning", "Windows Fast Startup is on",
                    "Fast Startup leaves Windows drives in a hibernated state, so Linux may mount them read-only or refuse to. Turn it off with powercfg /h off in an administrator terminal, or shut down with Shift while choosing Shut down. Do not run ntfsfix on a Windows installation you still use. Uoo does not change this."));
            else if (facts.FastStartupEnabled is null)
                findings.Add(new("fast_startup", "info", "Fast Startup could not be checked",
                    "If Linux cannot mount a Windows volume read-write, turn off Fast Startup (powercfg /h off). Uoo does not change this."));
            if (facts.ClockIsUtc != true)
                findings.Add(new("clock", "info", "If the clock is wrong after switching systems",
                    "Windows keeps local time in the hardware clock and most Linux systems expect UTC, so the time can be hours off. Use one of two fixes, not both: set RealTimeIsUniversal on Windows (reg add \"HKLM\\SYSTEM\\CurrentControlSet\\Control\\TimeZoneInformation\" /v RealTimeIsUniversal /t REG_DWORD /d 1 /f) or run 'timedatectl set-local-rtc 1' on Linux. Uoo does not change this."));
            if (facts.SecureBootEnabled == true)
                findings.Add(new("secure_boot_keys", "info", "Secure Boot keys are being replaced in 2026",
                    "Microsoft's 2011 Secure Boot certificates expire during 2026 (June 24, June 27, and October 19). Systems keep booting, and Windows and firmware updates add the 2023 certificates over time. Keep the Linux shim updated. If Linux fails with 'Verifying shim SBAT data failed' after a Windows update, update shim from a working session (see https://uoo.lolisoft.eu/docs/troubleshooting/). Updating the Secure Boot database changes what the TPM measures (PCR 7), so BitLocker or TPM-based LUKS unlocking can ask for the recovery key; keep it ready. Do not change Secure Boot keys by hand."));
        }
        return new() { Supported = true, Findings = findings, Steps = steps, Encrypted = encrypted };
    }

    /// <summary>Build one validated request from a step, always against the current snapshot.</summary>
    public static BootRequest BuildRequest(DualBootStep step, BootSnapshot snapshot)
    {
        ArgumentNullException.ThrowIfNull(step);
        ArgumentNullException.ThrowIfNull(snapshot);
        var order = snapshot.Firmware.Order;
        BootRequest request;
        switch (step.Action)
        {
            case DualBootAction.RegisterLoader:
                var loader = snapshot.LinuxLoaders.FirstOrDefault(l => l.Path.Equals(step.Target, StringComparison.OrdinalIgnoreCase))
                    ?? throw new ArgumentException("The Linux loader is no longer available on the system partition. Check dual boot again.");
                request = new() { Operation = BootOperation.AddFirmwareEntry, Description = loader.Distribution, EfiPartition = loader.Partition, EfiLoaderPath = loader.Path };
                break;
            case DualBootAction.IncludeInOrder:
                if (!snapshot.Firmware.Entries.Any(e => e.Id == step.Target)) throw new ArgumentException("The firmware entry is no longer available. Check dual boot again.");
                request = new() { Operation = BootOperation.SetFirmwareOrder, Order = order.Contains(step.Target, StringComparer.OrdinalIgnoreCase) ? [.. order] : [.. order, step.Target] };
                break;
            case DualBootAction.StartFirst:
                if (!snapshot.Firmware.Entries.Any(e => e.Id == step.Target)) throw new ArgumentException("The firmware entry is no longer available. Check dual boot again.");
                request = new() { Operation = BootOperation.SetFirmwareOrder, Order = [step.Target, .. order.Where(id => !id.Equals(step.Target, StringComparison.OrdinalIgnoreCase))] };
                break;
            default: throw new ArgumentException("Unsupported dual-boot step.");
        }
        request = request with { ExpectedRevision = snapshot.Revision };
        BootValidation.Validate(request, snapshot);
        return request;
    }
}
