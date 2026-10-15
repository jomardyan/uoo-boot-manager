using System.Diagnostics;
using System.Security.Cryptography;
using System.Text.Json;
using System.Runtime.InteropServices;
using System.ComponentModel;
using Uoo.Core;
using Uoo.Platform;

/// <summary>Opt-in change/restore validation on disposable GitHub-hosted Windows VMs only.</summary>
internal static class LiveTransactions
{
    public static async Task<int> RunAsync(string reportPath)
    {
        if (Environment.GetEnvironmentVariable("GITHUB_ACTIONS") != "true" ||
            Environment.GetEnvironmentVariable("RUNNER_ENVIRONMENT") != "github-hosted" ||
            Environment.GetEnvironmentVariable("UOO_DISPOSABLE_WINDOWS") != "1" || !WindowsMachine.Administrator)
            throw new InvalidOperationException("Live integration requires an elevated disposable GitHub-hosted Windows runner and explicit UOO_DISPOSABLE_WINDOWS=1.");
        var checks = new List<string>(); var skipped = new List<string>(); var errors = new List<string>();
        List<object> failures = [];
        object? repairEvidence = null;
        var service = new WindowsBootService();
        BootSnapshot? state = null; string baselineId = "", repairId = "", firmwareUndoId = "";
        string baseline = ReadSystemBcd();
        string baselineListing = ReadSystemBcdListing();
        string? firmwareVariables = null;
        try { firmwareVariables = ReadFirmwareVariables(); }
        catch (Exception error) when (error is Win32Exception or IOException or UnauthorizedAccessException)
        { skipped.Add("Direct UEFI-variable comparison unavailable: " + error.Message); }
        async Task<BootSnapshot> Send(BootRequest request)
        {
            var response = await service.ExecuteAsync(request with { ExpectedRevision = state?.Revision ?? "", RecoveryKeyAcknowledged = true });
            if (!response.Success || response.Snapshot is null)
            {
                failures.Add(new { operation = request.Operation.ToString(), response.Message, beforeMenu = state?.Menu, afterMenu = response.Snapshot?.Menu,
                    beforeFirmware = state?.Firmware, afterFirmware = response.Snapshot?.Firmware, snapshotErrors = response.Snapshot?.Errors });
                throw new InvalidOperationException(request.Operation + ": " + response.Message);
            }
            state = response.Snapshot;
            return state;
        }
        void Check(bool value, string name)
        {
            if (!value) throw new InvalidOperationException("Integration assertion failed: " + name);
            checks.Add(name); Console.WriteLine("PASS live " + name);
        }
        try
        {
            state = await Send(new() { Operation = BootOperation.Inspect });
            var original = BootJson.Clone(state);
            Check(state.Errors.Count == 0 && state.Entries.Count(e => e.IsCurrent && e.IsWindows) == 1, "Inspect running Windows BCD");
            string current = state.Entries.Single(e => e.IsCurrent).Id;
            var oldIds = state.Backups.Select(x => x.Id).ToHashSet();
            state = await Send(new() { Operation = BootOperation.CreateBackup, Description = "Disposable Windows integration baseline" });
            baselineId = state.Backups.Single(x => !oldIds.Contains(x.Id)).Id;
            Check(state.Backups.Single(x => x.Id == baselineId).CanRestore, "Create verified full baseline backup");

            state = await Send(new() { Operation = BootOperation.SetMenu, Menu = state.Menu with { DefaultId = current, Timeout = 17, DisplayMenu = !state.Menu.DisplayMenu } });
            Check(state.Menu.Timeout == 17 && state.Menu.DefaultId == current, "Apply live menu default, timeout and visibility");
            state = await Send(new() { Operation = BootOperation.CopyEntry, TargetId = current, Description = "Uoo disposable Windows profile" });
            var copied = state.Entries.Single(x => !original.Entries.Any(old => old.Id == x.Id));
            Check(state.Menu.Order.Contains(copied.Id) && !copied.IsCurrent && !copied.IsDefault, "Copy live Windows loader and append it to the menu");
            var options = new BootEntryOptions { SafeBoot = "Network", BootLog = true, Sos = true, BaseVideo = true, RecoveryEnabled = false, MenuPolicy = "Legacy" };
            state = await Send(new() { Operation = BootOperation.SetEntryOptions, TargetId = copied.Id, Options = options });
            Check(state.Entries.Single(x => x.Id == copied.Id).Options == options, "Apply and read back all supported options on the disposable profile");
            state = await Send(new() { Operation = BootOperation.RenameEntry, TargetId = copied.Id, Description = "Uoo — disposable renamed profile" });
            Check(state.Entries.Single(x => x.Id == copied.Id).Description == "Uoo — disposable renamed profile", "Rename live profile without altering its identity");
            state = await Send(new() { Operation = BootOperation.SetBootOnce, TargetId = copied.Id });
            Check(state.Menu.BootOnceId == copied.Id, "Schedule one-time Windows boot");
            state = await Send(new() { Operation = BootOperation.ClearBootOnce });
            Check(state.Menu.BootOnceId.Length == 0, "Clear one-time Windows boot");
            state = await Send(new() { Operation = BootOperation.DeleteEntry, TargetId = copied.Id });
            Check(state.Entries.All(x => x.Id != copied.Id), "Delete the unreferenced disposable Windows profile");

            var exported = await service.ExecuteAsync(new() { Operation = BootOperation.ExportConfiguration, ExpectedRevision = state.Revision });
            Check(exported.Success && exported.ConfigurationJson.Length > 0, "Export live typed configuration through data-only protocol");
            state = await Send(new() { Operation = BootOperation.ImportConfiguration, ConfigurationJson = exported.ConfigurationJson });
            Check(state.Menu.Timeout == 17, "Import typed configuration with native readback verification");

            if (state.Firmware.Available)
            {
                if (state.Firmware.Order.Count > 0)
                {
                    var reordered = state.Firmware.Order.AsEnumerable().Reverse().ToList();
                    state = await Send(new() { Operation = BootOperation.SetFirmwareOrder, Order = reordered });
                    Check(state.Firmware.Order.SequenceEqual(reordered), "Apply live firmware order without excluding registrations");
                }
                else skipped.Add("Firmware order: this virtual firmware exposes an empty BootOrder.");
                var target = state.Firmware.Entries.FirstOrDefault(x => x.IsWindowsBootManager) ?? state.Firmware.Entries.First();
                state = await Send(new() { Operation = BootOperation.SetFirmwareBootOnce, TargetId = target.Id });
                Check(state.Firmware.BootOnceId == target.Id, "Schedule one-time UEFI boot");
                state = await Send(new() { Operation = BootOperation.ClearFirmwareBootOnce });
                Check(state.Firmware.BootOnceId.Length == 0, "Clear one-time UEFI boot");
                if (state.Repair.Available && (state.Firmware.CurrentId.Length != 0 || WindowsMachine.BootSession().Length != 0))
                {
                    var before = state.Backups.Select(x => x.Id).ToHashSet();
                    state = await Send(new() { Operation = BootOperation.AddFirmwareEntry, Description = "Uoo disposable EFI registration", EfiPartition = state.Repair.SystemPartition, EfiLoaderPath = @"\EFI\Microsoft\Boot\bootmgfw.efi" });
                    firmwareUndoId = state.Backups.Single(x => !before.Contains(x.Id)).Id;
                    var added = state.Firmware.Entries.Single(x => x.Description == "Uoo disposable EFI registration");
                    Check(state.Firmware.Order.Contains(added.Id), "Register an existing native EFI loader");
                    state = await Send(new() { Operation = BootOperation.RestoreBackup, BackupId = firmwareUndoId, Confirmation = "RESTORE" });
                    firmwareUndoId = "";
                    Check(state.Firmware.Entries.All(x => x.Id != added.Id), "Undo EFI creation from its identity-bound backup");
                }
                else skipped.Add("EFI registration creation: current boot identity/session or system ESP is unavailable.");
            }
            else skipped.Add("Firmware mutation: " + state.Firmware.Reason);

            if (state.Repair.Available)
            {
                string esp = state.Repair.SystemPartition;
                var efiBefore = EfiDigest(esp);
                // Preserve the complete guest ESP before simulating a missing boot manager.
                // This outer recovery point survives even if repair's own rollback restores
                // the deliberately missing file. No guest reboot occurs during this suite.
                var protectedStore = new ProtectedStore();
                using (var bcd = new BcdStore())
                    repairId = protectedStore.Create(bcd, state, "Disposable EFI repair baseline", repair: WindowsMachine.ValidateEsp(esp, true)).Id;
                _ = protectedStore.Read(repairId);
                var target = Path.Combine(esp, @"EFI\Microsoft\Boot\bootmgfw.efi");
                File.Delete(target);
                state = await Send(new() { Operation = BootOperation.Repair, Confirmation = state.Repair.Confirmation });
                var sources = new[] { "EFI", "EFI_EX" }.SelectMany(directory => new[] { "bootmgfw.efi", "bootmgfw_EX.efi" }
                    .Select(name => Path.Combine(WindowsMachine.WindowsDirectory, "Boot", directory, name))).Where(File.Exists).ToList();
                string targetHash = File.Exists(target) ? ProtectedStore.Hash(target) : "";
                repairEvidence = new { targetHash, sources = sources.Select(path => new { path, hash = ProtectedStore.Hash(path) }).ToList() };
                Console.WriteLine("EFI repair evidence: " + JsonSerializer.Serialize(repairEvidence));
                Check(targetHash.Length != 0 && sources.Any(x => ProtectedStore.Hash(x) == targetHash), "Recreate a missing Microsoft EFI loader through real BCDBoot");
                state = await Send(new() { Operation = BootOperation.RestoreBackup, BackupId = repairId, Confirmation = "RESTORE" });
                repairId = "";
                Check(EfiDigest(esp) == efiBefore, "Restore byte-identical EFI files after real repair");
            }
            else skipped.Add("UEFI repair: " + state.Repair.Reason);

            state = await Send(new() { Operation = BootOperation.RestoreBackup, BackupId = baselineId, Confirmation = "RESTORE" });
            Check(ReadSystemBcd() == baseline, "Restore exact original system BCD and firmware inventory");
            Check(CanonicalFirmware(state.Firmware) == CanonicalFirmware(original.Firmware), "Restore original firmware entries, order and one-time choice");
            if (firmwareVariables is not null) Check(ReadFirmwareVariables() == firmwareVariables, "Preserve original native UEFI BootOrder and boot-option bytes");
            var store = new ProtectedStore();
            store.Journal(baselineId);
            using (var bcd = new BcdStore()) bcd.Integer(Bcd.Manager, Bcd.Timeout, 19);
            state = await Send(new() { Operation = BootOperation.Inspect });
            Check(ReadSystemBcd() == baseline && store.PendingId().Length == 0, "Recover an interrupted real BCD write from the protected journal");
        }
        catch (Exception ex) { errors.Add(ex.ToString()); Console.Error.WriteLine(ex); }
        finally
        {
            if (baselineId.Length != 0)
            {
                try
                {
                    state = await Send(new() { Operation = BootOperation.Inspect });
                    if (firmwareUndoId.Length != 0) state = await Send(new() { Operation = BootOperation.RestoreBackup, BackupId = firmwareUndoId, Confirmation = "RESTORE" });
                    if (repairId.Length != 0) state = await Send(new() { Operation = BootOperation.RestoreBackup, BackupId = repairId, Confirmation = "RESTORE" });
                    state = await Send(new() { Operation = BootOperation.RestoreBackup, BackupId = baselineId, Confirmation = "RESTORE" });
                    Check(ReadSystemBcd() == baseline, "Final cleanup preserves original boot state");
                    if (firmwareVariables is not null) Check(ReadFirmwareVariables() == firmwareVariables, "Final cleanup preserves native UEFI variables");
                }
                catch (Exception ex) { errors.Add("Cleanup: " + ex); Console.Error.WriteLine("Cleanup: " + ex); }
            }
            Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(reportPath))!);
            string finalListing;
            try { finalListing = ReadSystemBcdListing(); }
            catch (Exception error) { finalListing = "Unable to read final diagnostic listing: " + error.Message; }
            await File.WriteAllTextAsync(reportPath, JsonSerializer.Serialize(new { success = errors.Count == 0, disposableHostedRunner = true, environment = Environment.OSVersion.VersionString,
                architecture = System.Runtime.InteropServices.RuntimeInformation.ProcessArchitecture.ToString(), checks, skipped, errors, failures, repairEvidence,
                bcdListings = new { before = baselineListing, after = finalListing }, nativeFirmwareBaseline = firmwareVariables }, new JsonSerializerOptions { WriteIndented = true }));
        }
        Console.WriteLine($"{checks.Count} live transaction checks, {errors.Count} failures");
        return errors.Count == 0 ? 0 : 1;
    }

    private static string EfiDigest(string volume)
    {
        List<string> rows = [];
        foreach (var relative in new[] { @"EFI\Microsoft\Boot", @"EFI\Boot" })
        {
            string root = Path.Combine(volume, relative);
            if (!Directory.Exists(root)) continue;
            foreach (var path in Directory.EnumerateFiles(root, "*", SearchOption.AllDirectories))
            {
                string name = Path.GetRelativePath(volume, path);
                if (name.Equals(@"EFI\Microsoft\Boot\BCD", StringComparison.OrdinalIgnoreCase) || name.StartsWith(@"EFI\Microsoft\Boot\BCD.LOG", StringComparison.OrdinalIgnoreCase)) continue;
                rows.Add(name + "=" + ProtectedStore.Hash(path));
            }
        }
        rows.Sort(StringComparer.Ordinal);
        return Convert.ToHexString(SHA256.HashData(System.Text.Encoding.UTF8.GetBytes(string.Join("\n", rows))));
    }

    private static string CanonicalFirmware(FirmwareState firmware) =>
        JsonSerializer.Serialize(firmware with { Entries = firmware.Entries.OrderBy(entry => entry.Id, StringComparer.OrdinalIgnoreCase).ToList() });

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern uint GetFirmwareEnvironmentVariableEx(string name, string guid, byte[] value, uint size, out uint attributes);
    private static string ReadFirmwareVariables()
    {
        using var privilege = new TokenPrivilege("SeSystemEnvironmentPrivilege");
        SortedDictionary<string, string> values = new(StringComparer.Ordinal);
        byte[]? Read(string name, bool required = false)
        {
            byte[] buffer = new byte[64 * 1024];
            var length = GetFirmwareEnvironmentVariableEx(name, "{8be4df61-93ca-11d2-aa0d-00e098032b8c}", buffer, (uint)buffer.Length, out uint attributes);
            if (length == 0)
            {
                int error = Marshal.GetLastWin32Error();
                if (!required && error == 203) { values[name] = "absent"; return null; }
                throw new Win32Exception(error, "Could not read " + name);
            }
            var data = buffer.AsSpan(0, checked((int)length)).ToArray();
            values[name] = attributes.ToString("X8") + ":" + Convert.ToHexString(data);
            return data;
        }
        var order = Read("BootOrder", true)!;
        if (order.Length % 2 != 0 || order.Length > 4096) throw new IOException("Invalid native UEFI BootOrder size.");
        HashSet<ushort> ids = [];
        for (int i = 0; i < order.Length; i += 2) ids.Add(System.Buffers.Binary.BinaryPrimitives.ReadUInt16LittleEndian(order.AsSpan(i)));
        foreach (var name in new[] { "BootCurrent", "BootNext" })
        {
            var selection = Read(name);
            if (selection is null) continue;
            if (selection.Length != 2) throw new IOException("Invalid native UEFI selection size.");
            ids.Add(System.Buffers.Binary.BinaryPrimitives.ReadUInt16LittleEndian(selection));
        }
        foreach (var id in ids.Order()) _ = Read("Boot" + id.ToString("X4"), true);
        return JsonSerializer.Serialize(values);
    }

    private static string ReadSystemBcd()
    {
        using var bcd = new BcdStore();
        // Object enumeration order is not part of BCD state. Preserve every element's
        // full native payload, including list ordering, while sorting object/type keys.
        return JsonSerializer.Serialize(bcd.Enumerate().OrderBy(record => record.Id, StringComparer.OrdinalIgnoreCase)
            .Select(record => new { record.Id, record.Type, Elements = record.RawElements!.OrderBy(element => element.Key).ToArray() }));
    }

    private static string ReadSystemBcdListing()
    {
        var info = new ProcessStartInfo(Path.Combine(Environment.SystemDirectory, "bcdedit.exe")) { UseShellExecute = false, RedirectStandardOutput = true, RedirectStandardError = true, CreateNoWindow = true };
        foreach (var value in new[] { "/enum", "all", "/v" }) info.ArgumentList.Add(value);
        using var process = Process.Start(info)!;
        var output = process.StandardOutput.ReadToEnd(); var error = process.StandardError.ReadToEnd(); process.WaitForExit();
        if (process.ExitCode != 0) throw new IOException("Could not inspect the disposable runner's BCD: " + error);
        return output;
    }
}
