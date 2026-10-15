using System.IO;
using System.Text.Json;
using System.Windows;
using Uoo.Core;
using Uoo.Platform;

namespace Uoo.Desktop;

public sealed partial class App : Application
{
    protected override async void OnStartup(StartupEventArgs e)
    {
        base.OnStartup(e);
        try
        {
            var inspect = Array.IndexOf(e.Args, "--inspect-test");
            if (inspect >= 0)
            {
                if (inspect + 1 >= e.Args.Length || e.Args[inspect + 1].StartsWith("--", StringComparison.Ordinal)) throw new ArgumentException("--inspect-test requires an output file.");
                var response = await new ElevatedBootService().ExecuteAsync(new BootRequest { Operation = BootOperation.Inspect, Confirmation = "ADMIN" });
                await File.WriteAllTextAsync(e.Args[inspect + 1], JsonSerializer.Serialize(response, BootJson.Options));
                Shutdown(response.Success && response.Snapshot is not null ? 0 : 1);
                return;
            }
            var smoke = Array.IndexOf(e.Args, "--smoke-test");
            if (smoke >= 0 && (smoke + 1 >= e.Args.Length || e.Args[smoke + 1].StartsWith("--", StringComparison.Ordinal)))
                throw new ArgumentException("--smoke-test requires an output directory.");
            var demo = smoke >= 0 || e.Args.Contains("--demo");
            var window = MakeWindow(demo);
            MainWindow = window;
            window.Show();
            if (smoke >= 0)
            {
                await window.LoadAsync();
                await SmokeRunner.RunAsync(window, e.Args[smoke + 1]);
                Shutdown(0);
            }
        }
        catch (Exception error)
        {
            if (e.Args.Contains("--smoke-test") || e.Args.Contains("--inspect-test"))
            {
                Console.Error.WriteLine(error);
                Shutdown(1);
            }
            else
            {
                MessageBox.Show(error.Message, "Uoo could not start", MessageBoxButton.OK, MessageBoxImage.Error);
                Shutdown(1);
            }
        }
    }

    private MainWindow MakeWindow(bool demo)
    {
        var window = new MainWindow(demo ? new DemoBootService() : new ElevatedBootService(), demo);
        window.ModeRequested += (_, _) =>
        {
            if (!window.ConfirmDiscard()) return;
            var replacement = MakeWindow(!window.IsDemo);
            // RestoreBounds keeps the normal size of a maximized window; Manual stops CenterScreen moving it.
            var bounds = window.WindowState == WindowState.Normal ? new Rect(window.Left, window.Top, window.Width, window.Height) : window.RestoreBounds;
            replacement.WindowStartupLocation = WindowStartupLocation.Manual;
            replacement.Left = bounds.Left;
            replacement.Top = bounds.Top;
            replacement.Width = bounds.Width;
            replacement.Height = bounds.Height;
            replacement.WindowState = window.WindowState == WindowState.Maximized ? WindowState.Maximized : WindowState.Normal;
            MainWindow = replacement;
            window.AllowClose = true;
            replacement.Show();
            window.Close();
        };
        return window;
    }
}
