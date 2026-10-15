using System.ComponentModel;
using System.Diagnostics;
using System.IO.Pipes;
using System.Runtime.InteropServices;
using System.Security.AccessControl;
using System.Security.Principal;
using System.Security.Cryptography;
using System.Text.Json;
using Microsoft.Win32.SafeHandles;
using Uoo.Core;

namespace Uoo.Platform;

/// <summary>Normal-user desktop client. A single reviewed request crosses a SID-protected local pipe.</summary>
public sealed class ElevatedBootService : IBootService
{
    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool GetNamedPipeClientProcessId(SafePipeHandle pipe, out uint processId);
    private readonly SemaphoreSlim gate = new(1, 1);
    public async Task<BootResponse> ExecuteAsync(BootRequest request, CancellationToken cancellationToken = default)
    {
        await gate.WaitAsync(cancellationToken);
        try
        {
            string filePath = request.FilePath;
            if (request.Operation == BootOperation.ImportConfiguration && !string.IsNullOrWhiteSpace(filePath))
            {
                request = request with { FilePath = "", ConfigurationJson = await BootConfiguration.ReadFileAsync(filePath, cancellationToken) };
            }
            if (request.Operation == BootOperation.ExportConfiguration) request = request with { FilePath = "" };
            if (request.Operation == BootOperation.Inspect && request.Confirmation != "ADMIN")
                return await new WindowsBootService().ExecuteAsync(request, cancellationToken);
            ProtectedInstallation.Verify(AppContext.BaseDirectory);
            string name = "uoo-" + Convert.ToHexString(RandomNumberGenerator.GetBytes(32)).ToLowerInvariant();
            using var identity = WindowsIdentity.GetCurrent();
            var security = new PipeSecurity();
            security.SetAccessRuleProtection(true, false);
            security.SetOwner(identity.User!);
            security.AddAccessRule(new PipeAccessRule(new SecurityIdentifier(WellKnownSidType.NetworkSid, null), PipeAccessRights.FullControl, AccessControlType.Deny));
            security.AddAccessRule(new PipeAccessRule(identity.User!, PipeAccessRights.FullControl, AccessControlType.Allow));
            security.AddAccessRule(new PipeAccessRule(new SecurityIdentifier(WellKnownSidType.BuiltinAdministratorsSid, null), PipeAccessRights.FullControl, AccessControlType.Allow));
            // Administrators permits UAC credential elevation from a standard account;
            // the peer PID must still be the exact helper launched by this desktop.
            using var pipe = NamedPipeServerStreamAcl.Create(name, PipeDirection.InOut, 1, PipeTransmissionMode.Byte,
                PipeOptions.Asynchronous, 0, 0, security);
            using var deadline = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
            deadline.CancelAfter(TimeSpan.FromMinutes(8));
            string helper = Path.Combine(AppContext.BaseDirectory, "Uoo.BootHelper.exe");
            var start = new ProcessStartInfo(helper) { UseShellExecute = true, Verb = "runas", WorkingDirectory = AppContext.BaseDirectory };
            start.ArgumentList.Add("--pipe"); start.ArgumentList.Add(name);
            using var process = Process.Start(start) ?? throw new InvalidOperationException("Windows did not start the administrator helper.");
            var connected = pipe.WaitForConnectionAsync(deadline.Token);
            var exited = process.WaitForExitAsync(deadline.Token);
            if (await Task.WhenAny(connected, exited) == exited && !pipe.IsConnected)
                throw new InvalidOperationException("The administrator helper closed before connecting. Reinstall the application if this continues.");
            await connected;
            if (!GetNamedPipeClientProcessId(pipe.SafePipeHandle, out var peer) || peer != (uint)process.Id)
                throw new InvalidOperationException("The administrator connection did not come from the launched Uoo helper.");
            await PipeProtocol.WriteAsync(pipe, JsonSerializer.Serialize(request, BootJson.Options), deadline.Token);
            string json = await PipeProtocol.ReadAsync(pipe, deadline.Token);
            var response = JsonSerializer.Deserialize<BootResponse>(json, BootJson.Options)
                ?? throw new InvalidDataException("The administrator helper returned an empty result.");
            await process.WaitForExitAsync(deadline.Token);
            if (response.Success && request.Operation == BootOperation.ExportConfiguration && !string.IsNullOrWhiteSpace(filePath))
            {
                if (string.IsNullOrWhiteSpace(response.ConfigurationJson)) throw new InvalidDataException("The helper did not return a configuration to export.");
                await BootConfiguration.WriteFileAsync(filePath, response.ConfigurationJson, cancellationToken);
            }
            return response;
        }
        catch (Win32Exception error) when (error.NativeErrorCode == 1223)
        {
            return new() { Success = false, Message = "Administrator authentication was canceled. Your draft has been kept." };
        }
        catch (Exception error) when (error is IOException or InvalidDataException or InvalidOperationException or UnauthorizedAccessException or JsonException or Win32Exception or OperationCanceledException)
        {
            return new() { Success = false, Message = error is OperationCanceledException ? "The administrator operation timed out. Reload to inspect the final state before trying again." : error.Message };
        }
        finally { gate.Release(); }
    }
}
