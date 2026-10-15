using System.Text;
using System.Text.Json;

namespace Uoo.Core;

/// <summary>The same bounded configuration format is used by demo, desktop, and helper.</summary>
public static class BootConfiguration
{
    public const int MaximumBytes = 1024 * 1024;
    private static readonly UTF8Encoding Utf8 = new(false, true);

    public static string Export(BootSnapshot snapshot) => JsonSerializer.Serialize(new WindowsConfiguration
    {
        Menu = snapshot.Menu with { BootOnceId = "" },
        Entries = snapshot.Entries.Where(e => e.IsWindows).Select(e => new ConfigurationEntry
        { Id = e.Id, Description = e.Description, Options = e.Options }).ToList()
    }, BootJson.Options);

    public static WindowsConfiguration Parse(string json, BootSnapshot snapshot)
    {
        if (string.IsNullOrWhiteSpace(json) || Utf8.GetByteCount(json) > MaximumBytes)
            throw new ArgumentException("Configuration JSON must be no larger than 1 MiB.");
        BootJson.RejectDuplicateProperties(json);
        var config = JsonSerializer.Deserialize<WindowsConfiguration>(json, BootJson.Options)
            ?? throw new ArgumentException("Choose a Windows Uoo configuration file.");
        if (config.Format != "uoo-windows-configuration" || config.Version != 1 || config.Menu is null || config.Entries is null ||
            config.Entries.Count is < 1 or > 128 || config.Entries.Any(e => e is null) ||
            config.Entries.Select(e => e.Id).Distinct(StringComparer.OrdinalIgnoreCase).Count() != config.Entries.Count)
            throw new ArgumentException("Unsupported or malformed Uoo Windows configuration.");
        BootValidation.ValidateMenu(config.Menu, snapshot);
        if (config.Menu.BootOnceId is null || config.Menu.BootOnceId.Length != 0)
            throw new ArgumentException("Imported configuration cannot schedule a one-time boot.");
        foreach (var entry in config.Entries)
        {
            var id = BootValidation.Id(entry.Id);
            if (!snapshot.Entries.Any(e => e.IsWindows && e.Id.Equals(id, StringComparison.OrdinalIgnoreCase)))
                throw new ArgumentException("Configuration references a Windows entry that is no longer available.");
            BootValidation.Description(entry.Description);
            BootValidation.ValidateOptions(entry.Options);
        }
        return config with
        {
            Menu = config.Menu with { DefaultId = BootValidation.Id(config.Menu.DefaultId), Order = config.Menu.Order.Select(BootValidation.Id).ToList() },
            Entries = config.Entries.Select(e => e with { Id = BootValidation.Id(e.Id), Description = BootValidation.Description(e.Description) }).ToList()
        };
    }

    public static async Task<string> ReadFileAsync(string path, CancellationToken cancellationToken = default)
    {
        await using var file = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read, 4096, FileOptions.Asynchronous);
        // Bound the actual read, even if the file grows after it is opened.
        var bytes = new byte[MaximumBytes + 1];
        var count = await file.ReadAtLeastAsync(bytes, bytes.Length, throwOnEndOfStream: false, cancellationToken: cancellationToken);
        if (count > MaximumBytes) throw new InvalidDataException("Configuration files must be no larger than 1 MiB.");
        int start = count >= 3 && bytes[0] == 0xef && bytes[1] == 0xbb && bytes[2] == 0xbf ? 3 : 0;
        try { return Utf8.GetString(bytes, start, count - start); }
        catch (DecoderFallbackException error) { throw new InvalidDataException("Configuration files must contain valid UTF-8 JSON.", error); }
    }

    public static async Task WriteFileAsync(string path, string json, CancellationToken cancellationToken = default)
    {
        var destination = Path.GetFullPath(path);
        var temporary = Path.Combine(Path.GetDirectoryName(destination)!, ".uoo-export-" + Guid.NewGuid().ToString("N"));
        try
        {
            await using (var file = new FileStream(temporary, FileMode.CreateNew, FileAccess.Write, FileShare.None, 4096, FileOptions.Asynchronous))
            {
                await file.WriteAsync(Utf8.GetBytes(json), cancellationToken);
                file.Flush(true);
            }
            cancellationToken.ThrowIfCancellationRequested();
            File.Move(temporary, destination, true);
        }
        finally { if (File.Exists(temporary)) File.Delete(temporary); }
    }
}
