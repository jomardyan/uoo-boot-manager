using System.Buffers.Binary;
using System.Text;

namespace Uoo.Platform;

public static class PipeProtocol
{
    public const int MaximumBytes = 4 * 1024 * 1024;
    public static async Task<string> ReadAsync(Stream stream, CancellationToken token)
    {
        var header = new byte[4];
        await stream.ReadExactlyAsync(header, token);
        int length = BinaryPrimitives.ReadInt32LittleEndian(header);
        if (length is < 2 or > MaximumBytes) throw new InvalidDataException("Invalid administrator response size.");
        byte[] body = new byte[length];
        await stream.ReadExactlyAsync(body, token);
        return new UTF8Encoding(false, true).GetString(body);
    }
    public static async Task WriteAsync(Stream stream, string json, CancellationToken token)
    {
        byte[] body = Encoding.UTF8.GetBytes(json);
        if (body.Length is < 2 or > MaximumBytes) throw new InvalidDataException("The administrator request is too large.");
        var header = new byte[4];
        BinaryPrimitives.WriteInt32LittleEndian(header, body.Length);
        await stream.WriteAsync(header, token);
        await stream.WriteAsync(body, token);
        await stream.FlushAsync(token);
    }
}
