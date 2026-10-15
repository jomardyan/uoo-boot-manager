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
        Converters = { new ExactEnumConverter<BootOperation>() }
    };

    // JsonStringEnumConverter also accepts case variants and comma-joined flag lists such as "SetMenu, CopyEntry".
    private sealed class ExactEnumConverter<T> : JsonConverter<T> where T : struct, Enum
    {
        public override T Read(ref Utf8JsonReader reader, Type typeToConvert, JsonSerializerOptions options) =>
            reader.TokenType == JsonTokenType.String && reader.GetString() is { } name && Enum.GetNames<T>().Contains(name, StringComparer.Ordinal)
                ? Enum.Parse<T>(name) : throw new JsonException($"Unsupported {typeof(T).Name} value.");
        public override void Write(Utf8JsonWriter writer, T value, JsonSerializerOptions options) =>
            writer.WriteStringValue(Enum.IsDefined(value) ? value.ToString() : throw new JsonException($"Unsupported {typeof(T).Name} value."));
    }

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
