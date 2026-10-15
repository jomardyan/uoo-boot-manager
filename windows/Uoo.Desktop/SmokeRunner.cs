using System.IO;
using System.Text.Json;
using System.Windows;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Threading;
using Uoo.Core;

namespace Uoo.Desktop;

/// <summary>Runs only with DemoBootService. Captures actual WPF controls on Windows.</summary>
internal static class SmokeRunner
{
    internal static async Task RunAsync(MainWindow window, string directory)
    {
        if (!window.IsDemo || !window.Snapshot.Demo) throw new InvalidOperationException("Visual smoke tests require demo mode.");
        Directory.CreateDirectory(directory);
        var checks = new List<string>(); var images = new List<string>();
        try
        {
            foreach (var (theme, label) in new[] { (ThemeMode.Light, "light"), (ThemeMode.Dark, "dark") })
            {
                window.ThemeMode = theme;
                foreach (var page in MainWindow.PageNames)
                {
                    window.Navigate(page); await RenderAsync(window);
                    var file = page.ToLowerInvariant().Replace(' ', '-') + "-" + label + ".png";
                    Capture(window, Path.Combine(directory, file)); images.Add(file);
                }
            }
            window.Width = window.MinWidth; window.Height = window.MinHeight;
            foreach (var page in MainWindow.PageNames)
            {
                window.Navigate(page); await RenderAsync(window);
                var file = page.ToLowerInvariant().Replace(' ', '-') + "-minimum.png";
                Capture(window, Path.Combine(directory, file)); images.Add(file);
            }
            window.Width = 1160; window.Height = 760; window.ThemeMode = ThemeMode.Light;
            await window.ExerciseSmokeAsync(checks);
            var dialog = new ConfirmDialog(window, "Repair Windows boot files?", "The reviewed demo repair plan will rebuild boot files on the sample system partition.", "REPAIR", true);
            dialog.Show(); await RenderAsync(dialog);
            Assert(!dialog.ConfirmButton.IsEnabled, "Typed repair starts disabled", checks);
            dialog.ConfirmationBox.Text = "REPAIR";
            Assert(!dialog.ConfirmButton.IsEnabled, "BitLocker acknowledgment is required", checks);
            dialog.RecoveryKeyBox.IsChecked = true;
            Assert(dialog.ConfirmButton.IsEnabled, "Exact repair text and acknowledgment enable confirmation", checks);
            await RenderAsync(dialog);
            Capture(dialog, Path.Combine(directory, "repair-confirmation.png")); images.Add("repair-confirmation.png"); dialog.Close();
            Assert(window.Snapshot.Demo, "All operations stayed in demo mode", checks);
            await File.WriteAllTextAsync(Path.Combine(directory, "smoke-report.json"), JsonSerializer.Serialize(new { success = true, toolkit = "WPF Fluent", environment = Environment.OSVersion.ToString(), architecture = System.Runtime.InteropServices.RuntimeInformation.ProcessArchitecture.ToString(), checks, screenshots = images }, new JsonSerializerOptions { WriteIndented = true }));
        }
        catch (Exception ex)
        {
            await File.WriteAllTextAsync(Path.Combine(directory, "smoke-report.json"), JsonSerializer.Serialize(new { success = false, error = ex.ToString(), checks, screenshots = images }, new JsonSerializerOptions { WriteIndented = true }));
            throw;
        }
    }
    internal static void Assert(bool value, string message, List<string> checks)
    {
        if (!value) throw new InvalidOperationException("Smoke assertion failed: " + message);
        checks.Add(message);
    }
    private static async Task RenderAsync(Window window)
    {
        window.UpdateLayout(); await window.Dispatcher.InvokeAsync(() => { }, DispatcherPriority.ApplicationIdle); window.UpdateLayout();
    }
    private static void Capture(Window window, string filename)
    {
        var content = (FrameworkElement)window.Content; var dpi = VisualTreeHelper.GetDpi(window);
        var margin = content.Margin;
        var logicalWidth = content.ActualWidth + margin.Left + margin.Right;
        var logicalHeight = content.ActualHeight + margin.Top + margin.Bottom;
        var width = Math.Max(1, (int)Math.Ceiling(logicalWidth * dpi.DpiScaleX)); var height = Math.Max(1, (int)Math.Ceiling(logicalHeight * dpi.DpiScaleY));
        var bitmap = new RenderTargetBitmap(width, height, dpi.PixelsPerInchX, dpi.PixelsPerInchY, PixelFormats.Pbgra32);
        var drawing = new DrawingVisual();
        using (var context = drawing.RenderOpen())
        {
            context.DrawRectangle(window.ThemeMode == ThemeMode.Dark ? new SolidColorBrush(Color.FromRgb(32, 32, 32)) : new SolidColorBrush(Color.FromRgb(243, 243, 243)), null, new Rect(0, 0, logicalWidth, logicalHeight));
            context.DrawRectangle(new VisualBrush(content), null, new Rect(margin.Left, margin.Top, content.ActualWidth, content.ActualHeight));
        }
        bitmap.Render(drawing); var encoder = new PngBitmapEncoder(); encoder.Frames.Add(BitmapFrame.Create(bitmap));
        using var stream = File.Create(filename); encoder.Save(stream);
    }
}

public partial class MainWindow
{
    internal async Task ExerciseSmokeAsync(List<string> checks)
    {
        SkipConfirmationsForSmoke = true;
        void Check(bool value, string name) => SmokeRunner.Assert(value, name, checks);
        Navigate("Windows boot"); _timeout.Text = "17"; _displayMenu.IsChecked = false;
        Check(HasDrafts, "Editing boot menu creates a retained draft");
        Navigate("Diagnostics"); Navigate("Windows boot"); Check(_timeout.Text == "17", "Navigation preserves boot menu drafts");
        Check(await ApplyMenuAsync(), "Apply Windows boot menu");
        Check(Snapshot.Menu.Timeout == 17 && !Snapshot.Menu.DisplayMenu, "Saved menu matches controls"); Check(!HasDrafts, "Successful apply clears drafts");
        _menuOrder.SelectedIndex = 0; ExcludeMenuEntry();
        Check(_menuOrder.Items.Count == 2 && !HasDrafts, "Running Windows stays in the menu");
        _menuOrder.SelectedIndex = 1; ExcludeMenuEntry();
        Check(HasDrafts && await ApplyMenuAsync(), "Apply exclusion of a Windows menu entry");
        Check(Snapshot.Entries.Count == 2 && Snapshot.Menu.Order.Count == 1, "Exclusion retains the Windows registration");
        _menuEntryPicker.SelectedItem = _menuEntryPicker.Items.OfType<BootEntry>().Single(e => e.Id == DemoBootService.OtherWindowsId);
        IncludeMenuEntry(); IncludeMenuEntry();
        Check(_menuOrder.Items.Count == 2 && await ApplyMenuAsync(), "Re-include a hidden entry without duplicates");
        _menuOrder.SelectedIndex = 1; Move(_menuOrder, -1); Check(await ApplyMenuAsync(), "Apply reordered Windows entries");
        Check(Snapshot.Menu.Order[0] == DemoBootService.OtherWindowsId, "Windows boot order persisted");
        Navigate("Entry options"); _safeBoot.SelectedItem = "Network"; _bootLog.IsChecked = true; _sos.IsChecked = true; _baseVideo.IsChecked = true; _menuPolicy.SelectedItem = "Legacy";
        Check(await ApplyOptionsAsync(), "Apply advanced Windows startup options");
        Check(Snapshot.Entries.Single(x => x.Id == _optionsEntryId).Options.SafeBoot == "Network", "Safe mode options persisted");
        Check(await RunMutationAsync(new BootRequest { Operation = BootOperation.CopyEntry, TargetId = DemoBootService.OtherWindowsId, Description = "Smoke Windows copy" }, "Copy", "Copy demo entry"), "Copy Windows entry");
        var copied = Snapshot.Entries.Single(x => x.Description == "Smoke Windows copy");
        Check(await RunMutationAsync(new BootRequest { Operation = BootOperation.RenameEntry, TargetId = copied.Id, Description = "Smoke renamed entry" }, "Rename", "Rename demo entry"), "Rename Windows entry");
        Check(await RunMutationAsync(new BootRequest { Operation = BootOperation.SetBootOnce, TargetId = copied.Id }, "Next boot", "Schedule demo entry"), "Schedule next Windows boot");
        Check(!await RunMutationAsync(new BootRequest { Operation = BootOperation.DeleteEntry, TargetId = copied.Id }, "Delete", "Delete scheduled entry"), "Scheduled Windows entry deletion is blocked");
        Check(await RunMutationAsync(new BootRequest { Operation = BootOperation.ClearBootOnce }, "Clear", "Clear next boot"), "Clear next Windows boot");
        Check(await RunMutationAsync(new BootRequest { Operation = BootOperation.DeleteEntry, TargetId = copied.Id }, "Delete", "Delete demo copy"), "Delete copied Windows entry");
        Navigate("Firmware"); _firmwareOrder.SelectedIndex = 1; Move(_firmwareOrder, -1); Check(await ApplyFirmwareOrderAsync(), "Apply reordered UEFI entries");
        Check(await RunMutationAsync(new BootRequest { Operation = BootOperation.SetFirmwareBootOnce, TargetId = DemoBootService.FirmwareUbuntuId }, "Once", "Schedule demo EFI entry"), "Schedule next UEFI boot");
        Check(!await RunMutationAsync(new BootRequest { Operation = BootOperation.DeleteFirmwareEntry, TargetId = DemoBootService.FirmwareUbuntuId }, "Delete", "Delete scheduled entry"), "Scheduled firmware entry deletion is blocked");
        Check(await RunMutationAsync(new BootRequest { Operation = BootOperation.ClearFirmwareBootOnce }, "Clear", "Clear next firmware boot"), "Clear next UEFI boot");
        Check(await RunMutationAsync(new BootRequest { Operation = BootOperation.AddFirmwareEntry, Description = "Smoke EFI entry", EfiPartition = @"S:\", EfiLoaderPath = @"\EFI\uoo\smoke.efi" }, "Add", "Add sample loader"), "Register demo EFI loader");
        var added = Snapshot.Firmware.Entries.Single(x => x.Description == "Smoke EFI entry");
        Check(await RunMutationAsync(new BootRequest { Operation = BootOperation.DeleteFirmwareEntry, TargetId = added.Id }, "Delete", "Remove demo loader", added.Id), "Delete demo EFI registration");
        Navigate("Windows boot"); _timeout.Text = "23";
        Check(await RunMutationAsync(new BootRequest { Operation = BootOperation.CreateBackup, Description = "Smoke backup" }, "Backup", "Create demo backup", preserveDrafts: true), "Create manual backup");
        Check(_timeout.Text == "23" && HasDrafts, "Creating backup preserves unapplied menu changes");
        var manual = Snapshot.Backups.First(x => x.Description == "Smoke backup");
        Check(await RunMutationAsync(new BootRequest { Operation = BootOperation.DeleteBackup, BackupId = manual.Id }, "Delete backup", "Delete sample backup", preserveDrafts: true), "Delete manual backup");
        Check(_timeout.Text == "23" && HasDrafts, "Deleting backup preserves unapplied menu changes");
        Check(await ApplyMenuAsync(), "Draft applies after backup revision changes");
        var restore = Snapshot.Backups.First(x => x.CanRestore);
        Check(await RunMutationAsync(new BootRequest { Operation = BootOperation.RestoreBackup, BackupId = restore.Id, Confirmation = "RESTORE" }, "Restore", "Restore sample backup", "RESTORE"), "Restore demo backup");
        var path = Path.Combine(Path.GetTempPath(), "uoo-smoke-" + Guid.NewGuid().ToString("N") + ".json");
        try
        {
            Check(await RunMutationAsync(new BootRequest { Operation = BootOperation.ExportConfiguration, FilePath = path }, "Export", "Export sample configuration", preserveDrafts: true), "Export configuration to explicitly selected smoke file");
            Check(File.Exists(path), "Export file exists");
            Check(await RunMutationAsync(new BootRequest { Operation = BootOperation.ImportConfiguration, FilePath = path }, "Import", "Import sample configuration", "IMPORT"), "Import validated demo configuration");
        }
        finally { if (File.Exists(path)) File.Delete(path); }
        Check(await RunMutationAsync(new BootRequest { Operation = BootOperation.Repair, Confirmation = Snapshot.Repair.Confirmation }, "Repair", "Run demo repair plan", Snapshot.Repair.Confirmation), "Execute simulated repair plan"); Navigate("Overview");
    }
}
