using System.Buffers.Binary;
using System.Text;

namespace Uoo.Platform;

public static class PipeProtocol
{
    public const int MaximumBytes = 4 * 1024 * 1024;
    public static async Task<string> ReadAsync(Stream stream, CancellationToken cancellationToken)
    {
        var header = new byte[4];
        await stream.ReadExactlyAsync(header, cancellationToken);
        int length = BinaryPrimitives.ReadInt32LittleEndian(header);
        if (length is < 2 or > MaximumBytes) throw new InvalidDataException("Invalid administrator message size.");
        byte[] body = new byte[length];
        await stream.ReadExactlyAsync(body, cancellationToken);
        return new UTF8Encoding(false, true).GetString(body);
    }
    public static async Task WriteAsync(Stream stream, string json, CancellationToken cancellationToken)
    {
        byte[] body = Encoding.UTF8.GetBytes(json);
        if (body.Length is < 2 or > MaximumBytes) throw new InvalidDataException("Invalid administrator message size.");
        var header = new byte[4];
        BinaryPrimitives.WriteInt32LittleEndian(header, body.Length);
        await stream.WriteAsync(header, cancellationToken);
        await stream.WriteAsync(body, cancellationToken);
        await stream.FlushAsync(cancellationToken);
    }
}
