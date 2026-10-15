using System.Security.AccessControl;
using System.Security.Principal;

namespace Uoo.Platform;

/// <summary>Elevated code must live in the per-machine, administrator-owned install directory.</summary>
public static class ProtectedInstallation
{
    public static void Verify(string directory)
    {
        if (!OperatingSystem.IsWindows()) throw new PlatformNotSupportedException("Administrator actions require Windows.");
        string root = Path.GetFullPath(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles));
        string actual = Path.GetFullPath(directory).TrimEnd(Path.DirectorySeparatorChar);
        if (!actual.StartsWith(root.TrimEnd('\\') + "\\", StringComparison.OrdinalIgnoreCase))
            throw new InvalidOperationException("Install Uoo Boot Manager for all users before using administrator actions.");
        var info = new DirectoryInfo(actual);
        while (info is not null)
        {
            VerifyInfo(info);
            if (string.Equals(info.FullName.TrimEnd('\\'), root.TrimEnd('\\'), StringComparison.OrdinalIgnoreCase)) break;
            info = info.Parent;
        }
        VerifyChildren(new DirectoryInfo(actual));
        if (!File.Exists(Path.Combine(actual, "Uoo.BootHelper.exe")))
            throw new InvalidOperationException("The installed administrator helper is missing. Reinstall Uoo Boot Manager.");
    }

    private static void VerifyChildren(DirectoryInfo directory)
    {
        foreach (var item in directory.EnumerateFileSystemInfos())
        {
            VerifyInfo(item);
            if (item is DirectoryInfo child) VerifyChildren(child);
        }
    }

    private static void VerifyInfo(FileSystemInfo info)
    {
        if ((info.Attributes & FileAttributes.ReparsePoint) != 0)
            throw new InvalidOperationException("The installed application cannot contain redirected files or directories.");
        FileSystemSecurity security = info is DirectoryInfo directory ? directory.GetAccessControl() : ((FileInfo)info).GetAccessControl();
        var owner = (SecurityIdentifier?)security.GetOwner(typeof(SecurityIdentifier));
        var trustedOwners = new HashSet<string>(StringComparer.Ordinal)
        {
            "S-1-5-18", "S-1-5-32-544", // SYSTEM, Administrators
            "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464" // TrustedInstaller
        };
        if (owner is null || !trustedOwners.Contains(owner.Value))
            throw new InvalidOperationException("The application installation must be owned by Administrators or Windows servicing.");
        const FileSystemRights writable = FileSystemRights.WriteData | FileSystemRights.AppendData | FileSystemRights.WriteAttributes |
            FileSystemRights.WriteExtendedAttributes | FileSystemRights.Delete | FileSystemRights.DeleteSubdirectoriesAndFiles |
            FileSystemRights.ChangePermissions | FileSystemRights.TakeOwnership;
        foreach (FileSystemAccessRule rule in security.GetAccessRules(true, true, typeof(SecurityIdentifier)))
        {
            // CREATOR OWNER is an inherit-only template, not a permission on this file.
            if ((rule.PropagationFlags & PropagationFlags.InheritOnly) != 0) continue;
            var sid = (SecurityIdentifier)rule.IdentityReference;
            if (rule.AccessControlType == AccessControlType.Allow && (rule.FileSystemRights & writable) != 0 && !trustedOwners.Contains(sid.Value))
                throw new InvalidOperationException("The application installation is writable without administrator permission. Reinstall it.");
        }
    }
}
