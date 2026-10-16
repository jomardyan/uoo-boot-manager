using System.Text.RegularExpressions;

namespace Uoo.Core;

/// <summary>Plain-language advice for Windows, WMI, and BCD failures that report only a code or a terse sentence.</summary>
public static class FriendlyErrors
{
    private static readonly (Regex Pattern, string Advice)[] Rules =
    [
        (new(@"access is denied|UnauthorizedAccess|0x80070005|0x80041003", RegexOptions.IgnoreCase),
            "Windows denied access. Use Load as administrator and approve the prompt, and check that security software is not blocking Uoo's helper."),
        (new(@"not enough space on the disk|disk is full|no space left", RegexOptions.IgnoreCase),
            "The EFI System Partition may be full. Windows often creates a 100 MiB partition. Free space only by removing vendor folders you are sure are obsolete, or enlarge the partition with a backup first. Uoo does not resize partitions."),
        (new(@"being used by another process|sharing violation", RegexOptions.IgnoreCase),
            "Another program, often antivirus or backup software, has the file open. Close it, wait a moment, and try again."),
        (new(@"cannot find the (?:path|file)|could not find a part of the path|system cannot find", RegexOptions.IgnoreCase),
            "A boot file or partition is not available. Check Diagnostics, and that the EFI System Partition is present and online in Disk Management."),
        (new(@"RPC server is unavailable|invalid namespace|0x8004100E|provider load failure|WMI", RegexOptions.IgnoreCase),
            "Windows' management service (WMI) is not responding. Restart the PC, or run 'winmgmt /verifyrepository' in an administrator terminal, then try again."),
        (new(@"timed out|timeout", RegexOptions.IgnoreCase),
            "The operation took too long. Reload to see what actually changed before trying again."),
        (new(@"Fast Startup|Hibernation", RegexOptions.IgnoreCase),
            "Turn off Fast Startup (powercfg /h off in an administrator terminal), restart once, and try again."),
        (new(@"legacy BIOS", RegexOptions.IgnoreCase),
            "UEFI features need Windows installed in UEFI mode on a GPT disk. Converting a legacy installation is outside Uoo's scope."),
    ];

    /// <summary>Advice for the first matching rule, or an empty string.</summary>
    public static string Advice(string? message)
    {
        if (string.IsNullOrWhiteSpace(message)) return "";
        foreach (var (pattern, advice) in Rules)
            if (pattern.IsMatch(message)) return advice;
        return "";
    }

    /// <summary>The original message followed by advice when a rule matches and the advice is not already present.</summary>
    public static string Append(string? message)
    {
        message ??= "";
        var advice = Advice(message);
        return advice.Length == 0 || message.Contains(advice, StringComparison.Ordinal) ? message : message + "\n\nWhat this usually means: " + advice;
    }
}
