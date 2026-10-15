using System.Text.Json;
using System.Text.Json.Serialization;

namespace Uoo.Core;

public static class BootJson
{
    public static JsonSerializerOptions Options { get; } = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
        UnmappedMemberHandling = JsonUnmappedMemberHandling.Disallow,
        MaxDepth = 24,
        WriteIndented = true,
        Converters = { new JsonStringEnumConverter<BootOperation>(allowIntegerValues: false) }
    };

    public static T Clone<T>(T value) => JsonSerializer.Deserialize<T>(JsonSerializer.Serialize(value, Options), Options)!;

    public static void RejectDuplicateProperties(string json)
    {
        using var document = JsonDocument.Parse(json, new JsonDocumentOptions { MaxDepth = 24 });
        Visit(document.RootElement);
        static void Visit(JsonElement element)
        {
            if (element.ValueKind == JsonValueKind.Object)
            {
                HashSet<string> names = [];
                foreach (var property in element.EnumerateObject())
                {
                    if (!names.Add(property.Name)) throw new ArgumentException("Configuration JSON contains duplicate properties.");
                    Visit(property.Value);
                }
            }
            else if (element.ValueKind == JsonValueKind.Array) foreach (var child in element.EnumerateArray()) Visit(child);
        }
    }
}
