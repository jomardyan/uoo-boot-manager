using System.Diagnostics;
using System.IO.Pipes;
using System.Security.Principal;
using System.Text.Json;
using System.Text.RegularExpressions;
using Uoo.Core;
using Uoo.Platform;

// There is no command shell, script path, executable path, or arbitrary BCD verb in this protocol.
if (!OperatingSystem.IsWindows()) return 2;
if (args.Length != 2 || args[0] != "--pipe" || !Regex.IsMatch(args[1], @"^uoo-[0-9a-f]{64}\z", RegexOptions.CultureInvariant)) return 2;
using var identity = WindowsIdentity.GetCurrent();
if (!new WindowsPrincipal(identity).IsInRole(WindowsBuiltInRole.Administrator)) return 5;

using var lifetime = new CancellationTokenSource(TimeSpan.FromMinutes(8));
try
{
    using var pipe = new NamedPipeClientStream(".", args[1], PipeDirection.InOut,
        PipeOptions.Asynchronous, TokenImpersonationLevel.Identification);
    await pipe.ConnectAsync(60_000, lifetime.Token);
    var json = await PipeProtocol.ReadAsync(pipe, lifetime.Token);
    var request = JsonSerializer.Deserialize<BootRequest>(json, BootJson.Options)
        ?? throw new InvalidDataException("The administrator request was empty.");
    // Production elevated requests must come from the protected installation. An explicit
    // direct developer/offline test uses WindowsBootService without this transport.
    ProtectedInstallation.Verify(AppContext.BaseDirectory);
    BootResponse result;
    try { result = await new WindowsBootService().ExecuteAsync(request, lifetime.Token); }
    catch (Exception error) { result = new() { Success = false, Message = error.Message }; }
    await PipeProtocol.WriteAsync(pipe, JsonSerializer.Serialize(result, BootJson.Options), lifetime.Token);
    return result.Success ? 0 : 1;
}
catch (Exception error)
{
    Debug.WriteLine(error);
    return 1;
}
