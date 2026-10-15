using System.Security.AccessControl;
using System.Security.Cryptography;
using System.Security.Principal;
using System.Text.Json;
using System.Text.RegularExpressions;
using Uoo.Core;

namespace Uoo.Platform;

internal sealed record FirmwareImage(string Id, uint Type, string Description, string Path, BcdDevice? Device, string PartitionVolume = "");
internal sealed record SavedFile(string Path, long Length, string Sha256);
internal sealed record EfiTree(string Volume, List<string> ExistingDirectories, List<SavedFile> Files);
internal sealed record BackupManifest
{
    public int Version { get; init; } = 1;
    public string Id { get; init; } = "";
    public string Machine { get; init; } = "";
    public string Description { get; init; } = "";
    public DateTimeOffset CreatedAt { get; init; }
    public string BcdSha256 { get; init; } = "";
    public string CurrentId { get; init; } = "";
    public string BootSession { get; init; } = "";
    public List<FirmwareImage> Firmware { get; init; } = [];
    public List<string> FirmwareOrder { get; init; } = [];
    public List<string> FirmwareOnce { get; init; } = [];
    public string CreatedFirmwareId { get; init; } = "";
    public FirmwareImage? CreatedFirmware { get; init; }
    public EfiTree? Efi { get; init; }
}

internal sealed class ProtectedStore
{
    internal static readonly JsonSerializerOptions Json = new() { WriteIndented = true, PropertyNameCaseInsensitive = false,
        UnmappedMemberHandling = System.Text.Json.Serialization.JsonUnmappedMemberHandling.Disallow };
    private static readonly SecurityIdentifier Administrators = new(WellKnownSidType.BuiltinAdministratorsSid, null);
    private static readonly SecurityIdentifier SystemSid = new(WellKnownSidType.LocalSystemSid, null);
    private static readonly string[] EfiRoots = [@"EFI\Microsoft\Boot", @"EFI\Boot"];
    private readonly string root;
    public ProtectedStore() => root = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData), "Uoo", "BootManager");
    private string Folder(string id) => Path.Combine(root, Id(id));
    private static string Id(string id) => Regex.IsMatch(id, @"^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{32}$") ? id : throw new ArgumentException("Invalid backup identifier.");
    public bool Exists => Directory.Exists(root);
    public void Ensure()
    {
        var parent = Path.GetDirectoryName(root)!;
        foreach (var directory in new[] { parent, root })
        {
            WindowsMachine.SafePath(directory);
            if (!Directory.Exists(directory))
            {
                var acl = new DirectorySecurity(); acl.SetAccessRuleProtection(true, false); acl.SetOwner(Administrators);
                foreach (var sid in new[] { Administrators, SystemSid }) acl.AddAccessRule(new FileSystemAccessRule(sid,
                    FileSystemRights.FullControl, InheritanceFlags.ContainerInherit | InheritanceFlags.ObjectInherit, PropagationFlags.None, AccessControlType.Allow));
                new DirectoryInfo(directory).Create(acl);
            }
            Validate(directory, true);
        }
    }
    private static void Validate(string path, bool directory)
    {
        WindowsMachine.SafePath(path);
        FileSystemSecurity security = directory ? new DirectoryInfo(path).GetAccessControl() : new FileInfo(path).GetAccessControl();
        var owner = security.GetOwner(typeof(SecurityIdentifier));
        if (!Administrators.Equals(owner) && !SystemSid.Equals(owner)) throw new IOException("Boot backup storage is not owned by Administrators or SYSTEM.");
        const FileSystemRights writes = FileSystemRights.Write | FileSystemRights.Modify | FileSystemRights.Delete | FileSystemRights.ChangePermissions | FileSystemRights.TakeOwnership;
        foreach (FileSystemAccessRule rule in security.GetAccessRules(true, true, typeof(SecurityIdentifier)))
            if (rule.AccessControlType == AccessControlType.Allow && (rule.FileSystemRights & writes) != 0 &&
                !Administrators.Equals(rule.IdentityReference) && !SystemSid.Equals(rule.IdentityReference))
                throw new IOException("Boot backup storage grants write access to another principal.");
    }
    private static void Seal(string path, bool directory)
    {
        WindowsMachine.SafePath(path);
        if (directory)
        {
            var acl = new DirectorySecurity(); acl.SetOwner(Administrators); acl.SetAccessRuleProtection(true, false);
            foreach (var sid in new[] { Administrators, SystemSid }) acl.AddAccessRule(new FileSystemAccessRule(sid, FileSystemRights.FullControl,
                InheritanceFlags.ContainerInherit | InheritanceFlags.ObjectInherit, PropagationFlags.None, AccessControlType.Allow));
            new DirectoryInfo(path).SetAccessControl(acl);
        }
        else
        {
            var acl = new FileSecurity(); acl.SetOwner(Administrators); acl.SetAccessRuleProtection(true, false);
            foreach (var sid in new[] { Administrators, SystemSid }) acl.AddAccessRule(new FileSystemAccessRule(sid, FileSystemRights.FullControl, AccessControlType.Allow));
            new FileInfo(path).SetAccessControl(acl);
        }
    }
    public FileStream Lock()
    {
        Ensure();
        var path = Path.Combine(root, ".lock");
        if (!File.Exists(path)) { using (var file = new FileStream(path, FileMode.CreateNew, FileAccess.ReadWrite, FileShare.None)) file.Flush(true); Seal(path, false); }
        Validate(path, false);
        return new FileStream(path, FileMode.Open, FileAccess.ReadWrite, FileShare.None);
    }
    internal static string Hash(string path)
    { using var file = File.OpenRead(path); return Convert.ToHexString(SHA256.HashData(file)); }
    internal static void WriteJson<T>(string path, T value)
    {
        using (var file = new FileStream(path, FileMode.CreateNew, FileAccess.Write, FileShare.None))
        { JsonSerializer.Serialize(file, value, Json); file.Flush(true); }
        Seal(path, false);
    }

    public BackupManifest Create(BcdStore bcd, BootSnapshot snapshot, string description, FirmwareImage? createdFirmware = null, EspPartition? repair = null)
    {
        Ensure();
        var id = DateTimeOffset.UtcNow.ToString("yyyyMMddTHHmmssZ") + "-" + Guid.NewGuid().ToString("N");
        var stage = Path.Combine(root, ".pending-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(stage);
        try
        {
            var bcdFile = Path.Combine(stage, "store.bcd"); bcd.Export(bcdFile);
            using (var file = new FileStream(bcdFile, FileMode.Open, FileAccess.ReadWrite, FileShare.None)) file.Flush(true);
            var records = bcd.Enumerate();
            List<EspPartition> partitions;
            try { partitions = WindowsMachine.Partitions(); }
            catch (Exception error) when (error is System.Management.ManagementException or IOException or UnauthorizedAccessException) { partitions = []; }
            var firmwareIds = snapshot.Firmware.Entries.Select(e => e.Id).ToHashSet(StringComparer.OrdinalIgnoreCase);
            var images = records.Where(e => firmwareIds.Contains(e.Id)).Select(e => new FirmwareImage(e.Id, e.Type,
                e.Text(Bcd.Description), e.Text(Bcd.Path), e.Device,
                partitions.SingleOrDefault(p => p.Device.Equals(e.Device?.Path, StringComparison.OrdinalIgnoreCase))?.Volume ?? "")).ToList();
            var manager = snapshot.Firmware.Available ? bcd.Read(Bcd.Firmware) : null;
            var manifest = new BackupManifest { Id = id, Machine = WindowsMachine.MachineId, Description = description,
                CreatedAt = DateTimeOffset.UtcNow, BcdSha256 = Hash(bcdFile), CurrentId = snapshot.Entries.Single(e => e.IsCurrent).Id, BootSession = WindowsMachine.BootSession(),
                Firmware = images, FirmwareOrder = snapshot.Firmware.Order, FirmwareOnce = manager?.Ids(Bcd.Sequence) ?? [],
                CreatedFirmwareId = createdFirmware?.Id ?? "", CreatedFirmware = createdFirmware, Efi = repair is null ? null : CaptureTree(stage, repair) };
            WriteJson(Path.Combine(stage, "manifest.json"), manifest);
            foreach (var path in Directory.EnumerateFileSystemEntries(stage, "*", SearchOption.AllDirectories)) Seal(path, Directory.Exists(path));
            Seal(stage, true);
            Directory.Move(stage, Folder(id));
            return manifest;
        }
        catch { Directory.Delete(stage, true); throw; }
    }

    public List<BackupInfo> List()
    {
        if (!Exists) return [];
        Validate(root, true);
        List<BackupInfo> result = [];
        foreach (var directory in Directory.EnumerateDirectories(root).Where(p => Regex.IsMatch(Path.GetFileName(p), @"^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{32}$")))
        {
            var id = Path.GetFileName(directory);
            try { var value = Read(id); result.Add(new() { Id = id, Description = value.Description, CreatedAt = value.CreatedAt, CanRestore = true, Detail = value.Efi is null ? "BCD and firmware state" : "BCD, firmware, and system EFI files" }); }
            catch (Exception error) when (error is IOException or JsonException or ArgumentException)
            { result.Add(new() { Id = id, Description = "Damaged backup", CanRestore = false, Detail = error.Message }); }
        }
        return result.OrderByDescending(e => e.CreatedAt).ToList();
    }
    public BackupManifest Read(string id)
    {
        var folder = Folder(id); Validate(root, true); Validate(folder, true);
        var manifestPath = Path.Combine(folder, "manifest.json"); Validate(manifestPath, false);
        if (new FileInfo(manifestPath).Length > 2 * 1024 * 1024) throw new IOException("Backup manifest is too large.");
        var value = JsonSerializer.Deserialize<BackupManifest>(File.ReadAllText(manifestPath), Json) ?? throw new IOException("Backup manifest is empty.");
        if (value.Version != 1 || value.Id != id || value.Machine != WindowsMachine.MachineId || value.Firmware is null || value.FirmwareOrder is null || value.FirmwareOnce is null ||
            value.CreatedFirmwareId is null || value.CurrentId is null || value.BootSession is null || value.Description is null || value.BcdSha256 is null ||
            value.Firmware.Any(f => f is null || f.Id is null || f.Description is null || f.Path is null || f.PartitionVolume is null ||
                f.Device is { Path: null } or { AdditionalOptions: null }) || value.FirmwareOrder.Any(string.IsNullOrEmpty) || value.FirmwareOnce.Any(string.IsNullOrEmpty)) throw new IOException("This backup is malformed or does not belong to this Windows installation.");
        var bcdFile = BcdPath(id); Validate(bcdFile, false);
        if (new FileInfo(bcdFile).Length is < 4096 or > 64 * 1024 * 1024 || Hash(bcdFile) != value.BcdSha256) throw new IOException("BCD backup integrity check failed.");
        _ = Bcd.Id(value.CurrentId);
        if (value.BootSession.Length != 0 && !Regex.IsMatch(value.BootSession, @"^[0-9A-F]{64}$")) throw new IOException("Invalid backup boot-session identity.");
        if (value.CreatedFirmwareId.Length != 0) _ = Bcd.Id(value.CreatedFirmwareId);
        if (value.CreatedFirmwareId.Length != 0 && (value.CreatedFirmware is null || value.CreatedFirmware.Id != value.CreatedFirmwareId)) throw new IOException("Invalid created firmware identity.");
        if (value.Firmware.Count > 2048 || value.FirmwareOrder.Count > 2048 || value.FirmwareOnce.Count > 1 ||
            value.Firmware.Select(f => Bcd.Id(f.Id)).Distinct().Count() != value.Firmware.Count ||
            value.FirmwareOrder.Select(Bcd.Id).Distinct().Count() != value.FirmwareOrder.Count)
            throw new IOException("Invalid firmware backup inventory.");
        foreach (var entry in value.FirmwareOrder.Concat(value.FirmwareOnce))
            if (!value.Firmware.Any(f => f.Id.Equals(Bcd.Id(entry), StringComparison.OrdinalIgnoreCase))) throw new IOException("Firmware backup references a missing entry.");
        if (value.Efi is not null) ValidateTree(folder, value.Efi);
        return value;
    }
    public string BcdPath(string id) => Path.Combine(Folder(id), "store.bcd");
    private (string Path, Action Cleanup) WorkingBcd(BackupManifest manifest)
    {
        var source = BcdPath(manifest.Id); Validate(source, false);
        if (Hash(source) != manifest.BcdSha256) throw new IOException("BCD backup integrity check failed.");
        var directory = Path.Combine(root, ".work-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(directory); Seal(directory, true);
        var path = Path.Combine(directory, "store.bcd");
        try
        {
            File.Copy(source, path); Seal(path, false);
            using (var output = new FileStream(path, FileMode.Open, FileAccess.ReadWrite, FileShare.None)) output.Flush(true);
            if (Hash(path) != manifest.BcdSha256) throw new IOException("BCD recovery copy integrity check failed.");
        }
        catch { Directory.Delete(directory, true); throw; }
        void Cleanup()
        {
            try
            {
                // An importer must never cause its still-live hive to be deleted. WMI can
                // also retain an offline hive handle briefly; leave that protected copy alone.
                if (BcdStore.IsSystemHiveFile(path)) return;
                WindowsMachine.SafePath(directory);
                foreach (var item in Directory.EnumerateFileSystemEntries(directory, "*", SearchOption.AllDirectories)) WindowsMachine.SafePath(item);
                Directory.Delete(directory, true);
            }
            catch (Exception error) when (error is IOException or UnauthorizedAccessException or System.ComponentModel.Win32Exception) { }
        }
        return (path, Cleanup);
    }
    public BcdStore OpenBcd(BackupManifest manifest)
    {
        var work = WorkingBcd(manifest);
        try { return new BcdStore(work.Path, work.Cleanup); }
        catch { work.Cleanup(); throw; }
    }
    public void ImportBcd(BcdStore live, BackupManifest manifest)
    {
        var work = WorkingBcd(manifest);
        try
        {
            using var archive = new BcdStore(work.Path);
            live.RestoreCoreFrom(archive);
        }
        finally { work.Cleanup(); }
        if (Hash(BcdPath(manifest.Id)) != manifest.BcdSha256) throw new IOException("The immutable BCD backup changed during restore.");
    }
    public void Delete(string id)
    {
        if (PendingId() == id) throw new IOException("The interrupted transaction backup cannot be deleted.");
        var folder = Folder(id); Validate(folder, true);
        foreach (var path in Directory.EnumerateFileSystemEntries(folder, "*", SearchOption.AllDirectories))
            Validate(path, Directory.Exists(path));
        Directory.Delete(folder, true);
    }
    public void Journal(string id) => WriteJson(Path.Combine(root, "transaction.json"), new Dictionary<string, string> { ["backup"] = Id(id) });
    public string PendingId()
    {
        if (!Exists) return "";
        var path = Path.Combine(root, "transaction.json"); if (!File.Exists(path)) return "";
        Validate(path, false);
        if (new FileInfo(path).Length > 1024) throw new IOException("Invalid recovery journal.");
        var value = JsonSerializer.Deserialize<Dictionary<string, string>>(File.ReadAllText(path), Json);
        if (value is null || value.Count != 1 || !value.TryGetValue("backup", out var id)) throw new IOException("Invalid recovery journal.");
        return Id(id);
    }
    public void Complete() { var path = Path.Combine(root, "transaction.json"); if (File.Exists(path)) { Validate(path, false); File.Delete(path); } }

    private static bool ProviderBcd(string relative) => relative.Equals(@"EFI\Microsoft\Boot\BCD", StringComparison.OrdinalIgnoreCase) ||
        relative.StartsWith(@"EFI\Microsoft\Boot\BCD.LOG", StringComparison.OrdinalIgnoreCase);
    private static EfiTree CaptureTree(string stage, EspPartition esp)
    {
        List<SavedFile> files = []; List<string> directories = []; long size = 0;
        foreach (var relativeRoot in EfiRoots)
        {
            var source = Path.Combine(esp.Volume, relativeRoot); WindowsMachine.SafePath(source);
            if (!Directory.Exists(source)) continue;
            directories.Add(relativeRoot);
            foreach (var directory in Directory.EnumerateDirectories(source, "*", SearchOption.AllDirectories))
            { WindowsMachine.SafePath(directory); directories.Add(Path.GetRelativePath(esp.Volume, directory)); }
            foreach (var path in Directory.EnumerateFiles(source, "*", SearchOption.AllDirectories))
            {
                WindowsMachine.SafePath(path); var relative = Path.GetRelativePath(esp.Volume, path);
                if (ProviderBcd(relative)) continue; // ExportStore captures the BCD hive consistently; its transaction logs are regenerated by Windows.
                var length = new FileInfo(path).Length; size += length;
                if (files.Count >= 10000 || size > 512L * 1024 * 1024) throw new IOException("EFI backup exceeds the supported bounded size.");
                var target = Path.Combine(stage, "efi", relative); Directory.CreateDirectory(Path.GetDirectoryName(target)!);
                File.Copy(path, target);
                using (var copy = new FileStream(target, FileMode.Open, FileAccess.ReadWrite, FileShare.None)) copy.Flush(true);
                files.Add(new(relative, length, Hash(target)));
            }
        }
        return new(esp.Volume, directories, files);
    }
    private static bool TreePath(string path) => !Path.IsPathRooted(path) && !path.Any(c => char.IsControl(c) || "/:*?\"<>|".Contains(c)) &&
        path.Split('\\').All(p => p.Length != 0 && p is not "." and not ".." && !p.EndsWith(' ') && !p.EndsWith('.')) &&
        EfiRoots.Any(root => path.Equals(root, StringComparison.OrdinalIgnoreCase) || path.StartsWith(root + "\\", StringComparison.OrdinalIgnoreCase));
    private static void ValidateTree(string folder, EfiTree tree)
    {
        if (tree.Volume is null || tree.Files is null || tree.ExistingDirectories is null || tree.Files.Any(f => f is null || f.Path is null || f.Sha256 is null) ||
            tree.ExistingDirectories.Any(p => p is null) || tree.Files.Count > 10000 || tree.Files.Any(f => f.Length is < 0 or > 512L * 1024 * 1024) || tree.Files.Sum(f => f.Length) > 512L * 1024 * 1024 ||
            tree.Files.Select(f => f.Path).Distinct(StringComparer.OrdinalIgnoreCase).Count() != tree.Files.Count ||
            tree.ExistingDirectories.Distinct(StringComparer.OrdinalIgnoreCase).Count() != tree.ExistingDirectories.Count)
            throw new IOException("Invalid EFI backup file list.");
        foreach (var dir in tree.ExistingDirectories) if (!TreePath(dir)) throw new IOException("Invalid EFI backup directory.");
        foreach (var item in tree.Files)
        {
            if (!TreePath(item.Path) || ProviderBcd(item.Path) || item.Length < 0) throw new IOException("Invalid EFI backup file.");
            if (tree.ExistingDirectories.Contains(item.Path, StringComparer.OrdinalIgnoreCase) ||
                tree.Files.Any(f => f.Path.StartsWith(item.Path + "\\", StringComparison.OrdinalIgnoreCase)) ||
                !tree.ExistingDirectories.Contains(Path.GetDirectoryName(item.Path)!, StringComparer.OrdinalIgnoreCase))
                throw new IOException("EFI backup contains conflicting file and directory paths.");
            var path = Path.Combine(folder, "efi", item.Path); Validate(path, false);
            if (new FileInfo(path).Length != item.Length || Hash(path) != item.Sha256) throw new IOException("EFI backup integrity check failed.");
        }
    }
    public void RestoreTree(BackupManifest manifest)
    {
        if (manifest.Efi is null) return;
        var tree = manifest.Efi; ValidateTree(Folder(manifest.Id), tree);
        var esp = WindowsMachine.ValidateEsp(tree.Volume, true);
        // Preflight every destination before deleting or replacing anything.
        foreach (var rootPath in EfiRoots)
        {
            var directory = Path.Combine(esp.Volume, rootPath); WindowsMachine.SafePath(directory);
            if (Directory.Exists(directory)) foreach (var path in Directory.EnumerateFileSystemEntries(directory, "*", SearchOption.AllDirectories)) WindowsMachine.SafePath(path);
        }
        foreach (var rootPath in EfiRoots)
        {
            var directory = Path.Combine(esp.Volume, rootPath); if (!Directory.Exists(directory)) continue;
            foreach (var path in Directory.EnumerateFiles(directory, "*", SearchOption.AllDirectories))
            {
                var relative = Path.GetRelativePath(esp.Volume, path);
                if (!ProviderBcd(relative) && !tree.Files.Any(f => f.Path.Equals(relative, StringComparison.OrdinalIgnoreCase))) File.Delete(path);
            }
        }
        foreach (var item in tree.Files)
        {
            var target = Path.Combine(esp.Volume, item.Path); Directory.CreateDirectory(Path.GetDirectoryName(target)!);
            var temporary = target + ".uoo-" + Guid.NewGuid().ToString("N");
            try
            {
                File.Copy(Path.Combine(Folder(manifest.Id), "efi", item.Path), temporary);
                using (var file = new FileStream(temporary, FileMode.Open, FileAccess.ReadWrite, FileShare.None)) file.Flush(true);
                File.Move(temporary, target, true);
                if (Hash(target) != item.Sha256) throw new IOException("Restored EFI file did not pass its integrity check.");
            }
            finally { if (File.Exists(temporary)) File.Delete(temporary); }
        }
        foreach (var relative in tree.ExistingDirectories.OrderBy(p => p.Length)) Directory.CreateDirectory(Path.Combine(esp.Volume, relative));
        foreach (var rootPath in EfiRoots)
        {
            var directory = Path.Combine(esp.Volume, rootPath); if (!Directory.Exists(directory)) continue;
            foreach (var child in Directory.EnumerateDirectories(directory, "*", SearchOption.AllDirectories).OrderByDescending(p => p.Length))
                if (!tree.ExistingDirectories.Contains(Path.GetRelativePath(esp.Volume, child), StringComparer.OrdinalIgnoreCase) && !Directory.EnumerateFileSystemEntries(child).Any()) Directory.Delete(child);
            if (!tree.ExistingDirectories.Contains(rootPath, StringComparer.OrdinalIgnoreCase) && !Directory.EnumerateFileSystemEntries(directory).Any()) Directory.Delete(directory);
        }
    }
}
