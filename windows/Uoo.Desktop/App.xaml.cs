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
                if (inspect + 1 >= e.Args.Length) throw new ArgumentException("--inspect-test requires an output file.");
                var response = await new ElevatedBootService().ExecuteAsync(new BootRequest { Operation = BootOperation.Inspect, Confirmation = "ADMIN" });
                await File.WriteAllTextAsync(e.Args[inspect + 1], JsonSerializer.Serialize(response, BootJson.Options));
                Shutdown(response.Success && response.Snapshot is not null ? 0 : 1);
                return;
            }
            var smoke = Array.IndexOf(e.Args, "--smoke-test");
            var demo = smoke >= 0 || e.Args.Contains("--demo");
            var window = MakeWindow(demo);
            MainWindow = window;
            window.Show();
            if (smoke >= 0)
            {
                if (smoke + 1 >= e.Args.Length) throw new ArgumentException("--smoke-test requires an output directory.");
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
            replacement.Left = window.Left;
            replacement.Top = window.Top;
            replacement.Width = window.Width;
            replacement.Height = window.Height;
            MainWindow = replacement;
            window.AllowClose = true;
            replacement.Show();
            window.Close();
        };
        return window;
    }
}
