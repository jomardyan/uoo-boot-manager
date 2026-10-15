using System.ComponentModel;
using System.IO;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using Microsoft.Win32;
using Uoo.Core;

namespace Uoo.Desktop;

public sealed partial class MainWindow : Window
{
    internal static readonly string[] PageNames = ["Overview", "Windows boot", "Entry options", "Firmware", "Backups", "Diagnostics", "Repair"];
    private static readonly string[] PageIcons = ["\uE80F", "\uE7F4", "\uE713", "\uE950", "\uE8F1", "\uE9D9", "\uE90F"];
    private readonly IBootService service;
    private readonly bool demo;
    private BootSnapshot? snapshot;
    private readonly Dictionary<string, FrameworkElement> pages = [];
    private bool busy, rendering;
    private Task? loadTask;
    private string currentPage = "Overview";
    private string optionsEntryId = "";
    private string menuBaseline = "", optionsBaseline = "", firmwareBaseline = "";
    private ComboBox defaultEntry = new(), menuEntryPicker = new(), entryPicker = new(), safeBoot = new(), menuPolicy = new(), firmwarePicker = new();
    private TextBox timeout = new();
    private CheckBox displayMenu = new(), bootLog = new(), sos = new(), baseVideo = new(), recovery = new();
    private ListBox menuOrder = new(), firmwareOrder = new(), backups = new();
    private TextBlock backupDetail = new();
    private Button restoreBackup = new(), deleteBackup = new();
    internal bool SkipConfirmationsForSmoke { get; set; }
    internal BootSnapshot Snapshot => snapshot ?? throw new InvalidOperationException("Settings have not loaded.");
    public bool IsDemo => demo;
    internal bool AllowClose { get; set; }
    public event EventHandler? ModeRequested;

    public MainWindow(IBootService service, bool demo = false)
    {
        this.service = service; this.demo = demo;
        InitializeComponent();
        ModeBadge.Text = demo ? "DEMO" : "LIVE";
        ModeButton.Content = demo ? "Open live mode" : "Open demo";
        AdminButton.Visibility = demo ? Visibility.Collapsed : Visibility.Visible;
        for (var i = 0; i < PageNames.Length; i++)
        {
            var item = new ListBoxItem { Tag = PageNames[i], Padding = new Thickness(12, 11, 12, 11), Margin = new Thickness(0, 2, 0, 2) };
            var row = new StackPanel { Orientation = Orientation.Horizontal };
            row.Children.Add(new TextBlock { Text = PageIcons[i], FontFamily = new System.Windows.Media.FontFamily("Segoe Fluent Icons"), FontSize = 18, Width = 32 });
            row.Children.Add(Ui.Text(PageNames[i])); item.Content = row; Ui.Name(item, PageNames[i]); Navigation.Items.Add(item);
        }
        Navigation.SelectedIndex = 0;
        Loaded += async (_, _) => await LoadAsync();
        Closing += OnClosing;
        PreviewKeyDown += async (_, e) =>
        {
            if (busy || snapshot is null) return;
            if (Keyboard.Modifiers == ModifierKeys.Control && e.Key == Key.R) { e.Handled = true; await RefreshAsync(); }
            else if (Keyboard.Modifiers == ModifierKeys.Control && e.Key == Key.S)
            {
                e.Handled = true;
                if (currentPage == "Windows boot") await ApplyMenuAsync();
                else if (currentPage == "Entry options") await ApplyOptionsAsync();
                else if (currentPage == "Firmware") await ApplyFirmwareOrderAsync();
            }
        };
    }

    internal Task LoadAsync() => loadTask ??= ExecuteAsync(new BootRequest { Operation = BootOperation.Inspect }, false, false);

    private async void Refresh_Click(object sender, RoutedEventArgs e) => await RefreshAsync();
    private async void Admin_Click(object sender, RoutedEventArgs e)
    {
        if (!ConfirmDiscard()) return;
        await ExecuteAsync(new BootRequest { Operation = BootOperation.Inspect, Confirmation = "ADMIN" }, false, false);
    }
    private void Mode_Click(object sender, RoutedEventArgs e) { if (!busy) ModeRequested?.Invoke(this, EventArgs.Empty); }
    internal async Task RefreshAsync()
    {
        if (busy || !ConfirmDiscard()) return;
        // A complete view came from the administrator helper; reading it again unelevated would hide it.
        var elevated = !demo && snapshot is { RequiresAdministrator: false };
        await ExecuteAsync(new BootRequest { Operation = BootOperation.Inspect, Confirmation = elevated ? "ADMIN" : "" }, false, false);
    }

    private void Navigation_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (Navigation.SelectedItem is not ListBoxItem item) return;
        currentPage = (string)item.Tag;
        if (pages.TryGetValue(currentPage, out var page)) PageHost.Content = page;
    }
    internal void Navigate(string page)
    {
        var index = Array.IndexOf(PageNames, page);
        if (index < 0) throw new ArgumentException("Unknown page.");
        Navigation.SelectedIndex = index;
    }

    internal bool HasDrafts => snapshot is not null &&
        (MenuFingerprint() != menuBaseline || OptionsFingerprint() != optionsBaseline || FirmwareFingerprint() != firmwareBaseline);
    public bool ConfirmDiscard()
    {
        if (busy) return false;
        return !HasDrafts || Confirm("Discard unsaved changes?", "Your unapplied boot menu, entry options, or firmware order changes will be discarded.", null, false, out _);
    }
    private void OnClosing(object? sender, CancelEventArgs e)
    {
        if (AllowClose || ConfirmDiscard()) return;
        e.Cancel = true;
        if (busy) StatusText.Text = "Wait for the current operation to finish before closing Uoo.";
    }

    private bool Confirm(string title, string summary, string? required, bool recovery, out bool acknowledged)
    {
        if (SkipConfirmationsForSmoke) { acknowledged = recovery; return true; }
        var dialog = new ConfirmDialog(this, title, summary, required, recovery);
        var accepted = dialog.ShowDialog() == true; acknowledged = accepted && dialog.RecoveryKeyAcknowledged; return accepted;
    }

    internal async Task<bool> RunMutationAsync(BootRequest request, string title, string summary, string? required = null, bool preserveDrafts = false)
    {
        if (busy || snapshot is null) return false;
        request = request with { ExpectedRevision = snapshot.Revision };
        try { BootValidation.Validate(request, snapshot); }
        catch (ArgumentException error) { ShowError(error.Message); return false; }
        if (HasDrafts && !preserveDrafts) summary += "\n\nAny unapplied changes on other pages will be reloaded after this operation.";
        var protectedChange = request.Operation is not (BootOperation.CreateBackup or BootOperation.DeleteBackup or BootOperation.ExportConfiguration);
        if (!Confirm(title, summary, required, protectedChange && BootValidation.RequiresRecoveryKey(snapshot), out var acknowledged)) return false;
        request = request with { RecoveryKeyAcknowledged = acknowledged };
        return await ExecuteAsync(request, true, preserveDrafts);
    }

    private async Task<bool> ExecuteAsync(BootRequest request, bool mutation, bool preserveDrafts)
    {
        if (busy) return false;
        SetBusy(true, request.Operation == BootOperation.Inspect ? "Reading boot configuration…" : demo ? "Applying demo changes…" : "Waiting for administrator approval and applying changes…");
        try
        {
            var response = await Task.Run(() => service.ExecuteAsync(request));
            if (!response.Success || response.Snapshot is null)
            {
                // A failed change can still report the settings that are now saved (recovery or rollback).
                if (response.Snapshot is { } current && snapshot is not null && pages.Count > 0)
                {
                    var changed = current.Revision != snapshot.Revision;
                    snapshot = current;
                    if (changed) RenderSnapshot(); else RefreshMetadata();
                }
                ShowError(string.IsNullOrWhiteSpace(response.Message) ? "The operation did not complete. Your unapplied changes are still available." : response.Message);
                return false;
            }
            snapshot = response.Snapshot;
            if (preserveDrafts && pages.Count > 0) RefreshMetadata(); else RenderSnapshot();
            StatusText.Text = string.IsNullOrWhiteSpace(response.Message) ? "Boot configuration loaded." : response.Message;
            return true;
        }
        catch (Exception error)
        {
            ShowError(error.Message + (mutation ? " Your unapplied changes are still available." : ""));
            return false;
        }
        finally { SetBusy(false); }
    }

    private void SetBusy(bool busy, string? message = null)
    {
        this.busy = busy; PageHost.IsEnabled = !busy; Navigation.IsEnabled = !busy;
        RefreshButton.IsEnabled = !busy; AdminButton.IsEnabled = !busy; ModeButton.IsEnabled = !busy;
        BusyProgress.Visibility = busy ? Visibility.Visible : Visibility.Collapsed;
        if (message is not null) StatusText.Text = message;
    }
    private void ShowError(string message)
    {
        StatusText.Text = message;
        if (!SkipConfirmationsForSmoke) MessageBox.Show(this, message, "Uoo Boot Manager", MessageBoxButton.OK, MessageBoxImage.Warning);
    }

    private void RenderSnapshot()
    {
        rendering = true;
        try
        {
            var snapshot = Snapshot;
            if (!snapshot.Entries.Any(x => x.Id == optionsEntryId && x.IsWindows)) optionsEntryId = snapshot.Entries.FirstOrDefault(x => x.IsCurrent && x.IsWindows)?.Id ?? snapshot.Entries.FirstOrDefault(x => x.IsWindows)?.Id ?? "";
            pages.Clear();
            pages["Overview"] = BuildOverview(); pages["Windows boot"] = BuildMenu();
            pages["Entry options"] = BuildOptions(); pages["Firmware"] = BuildFirmware();
            pages["Backups"] = BuildBackups(); pages["Diagnostics"] = BuildDiagnostics(); pages["Repair"] = BuildRepair();
            PageHost.Content = pages[currentPage];
            menuBaseline = MenuFingerprint(); optionsBaseline = OptionsFingerprint(); firmwareBaseline = FirmwareFingerprint();
            NoticeText.Text = demo ? "Demo mode — explore every feature using sample boot entries. No boot settings on this PC are changed."
                : snapshot.RequiresAdministrator ? "Some boot settings need administrator access. Load them as administrator to view and manage this PC's complete configuration."
                : "Changes apply to this PC. Windows asks for administrator approval when needed. You choose when to restart.";
            if (snapshot.Errors.Count > 0) NoticeText.Text += " " + string.Join(" ", snapshot.Errors);
        }
        finally { rendering = false; }
    }

    private void RefreshMetadata()
    {
        pages["Overview"] = BuildOverview(); pages["Backups"] = BuildBackups();
        if (currentPage is "Overview" or "Backups") PageHost.Content = pages[currentPage];
    }
    private BootEntry? SelectedWindows => entryPicker.SelectedItem as BootEntry;
    private FirmwareEntry? SelectedFirmware => firmwarePicker.SelectedItem as FirmwareEntry;
    private BackupInfo? SelectedBackup => (backups.SelectedItem as ListBoxItem)?.Tag as BackupInfo;
    private static string Fingerprint(object value) => JsonSerializer.Serialize(value, BootJson.Options);
    private string MenuFingerprint() => snapshot is null ? "" : Fingerprint(new { Default = (defaultEntry.SelectedItem as BootEntry)?.Id, Timeout = timeout.Text, Show = displayMenu.IsChecked, Order = menuOrder.Items.OfType<BootEntry>().Select(x => x.Id).ToList() });
    private string OptionsFingerprint() => snapshot is null ? "" : Fingerprint(new { Id = optionsEntryId, Options = ReadOptions() });
    private string FirmwareFingerprint() => snapshot is null ? "" : Fingerprint(firmwareOrder.Items.OfType<FirmwareEntry>().Select(x => x.Id).ToList());
    private BootEntryOptions ReadOptions() => new() { SafeBoot = safeBoot.SelectedItem as string ?? "Off", BootLog = bootLog.IsChecked == true, Sos = sos.IsChecked == true, BaseVideo = baseVideo.IsChecked == true, RecoveryEnabled = recovery.IsChecked == true, MenuPolicy = menuPolicy.SelectedItem as string ?? "Standard" };

    internal async Task<bool> ApplyMenuAsync()
    {
        if (!int.TryParse(timeout.Text, out var seconds) || seconds is < 0 or > 300) { ShowError("Enter a boot menu timeout of 0–300 seconds."); return false; }
        var menu = new BootMenuSettings { DefaultId = (defaultEntry.SelectedItem as BootEntry)?.Id ?? "", Timeout = seconds, DisplayMenu = displayMenu.IsChecked == true, Order = menuOrder.Items.OfType<BootEntry>().Select(x => x.Id).ToList() };
        return await RunMutationAsync(new BootRequest { Operation = BootOperation.SetMenu, Menu = menu }, "Apply Windows boot settings?", $"Default: {(defaultEntry.SelectedItem as BootEntry)?.Description}\nTimeout: {seconds} seconds\nShow boot menu: {(menu.DisplayMenu ? "Yes" : "No")}\n\nBoot order:\n" + string.Join("\n", menuOrder.Items.OfType<BootEntry>().Select((x, i) => $"{i + 1}. {x.Description}")));
    }
    internal async Task<bool> ApplyOptionsAsync()
    {
        if (SelectedWindows is not { } entry) { ShowError("Choose a Windows boot entry first."); return false; }
        var options = ReadOptions();
        return await RunMutationAsync(new BootRequest { Operation = BootOperation.SetEntryOptions, TargetId = entry.Id, Options = options }, "Apply entry options?", $"Entry: {entry.Description}\nSafe mode: {options.SafeBoot}\nBoot log: {options.BootLog}\nShow driver names: {options.Sos}\nBasic video: {options.BaseVideo}\nRecovery: {options.RecoveryEnabled}\nBoot menu style: {options.MenuPolicy}");
    }
    internal async Task<bool> ApplyFirmwareOrderAsync() => await RunMutationAsync(new BootRequest { Operation = BootOperation.SetFirmwareOrder, Order = firmwareOrder.Items.OfType<FirmwareEntry>().Select(x => x.Id).ToList() }, "Apply firmware boot order?", "The firmware will try entries in this order:\n\n" + string.Join("\n", firmwareOrder.Items.OfType<FirmwareEntry>().Select((x, i) => $"{i + 1}. {x.Description}")));

    private void IncludeMenuEntry()
    {
        if (menuEntryPicker.SelectedItem is not BootEntry entry) return;
        if (!menuOrder.Items.OfType<BootEntry>().Any(x => x.Id == entry.Id)) menuOrder.Items.Add(entry);
        menuOrder.SelectedItem = entry;
    }
    private void ExcludeMenuEntry()
    {
        if (menuOrder.SelectedItem is not BootEntry entry) return;
        if (entry.IsCurrent || (defaultEntry.SelectedItem as BootEntry)?.Id == entry.Id || menuOrder.Items.Count <= 1)
        { ShowError("Keep the running Windows entry and the selected default in the boot menu."); return; }
        menuOrder.Items.Remove(entry);
    }

    private void Move(ListBox list, int delta)
    {
        var old = list.SelectedIndex; var next = old + delta;
        if (old < 0 || next < 0 || next >= list.Items.Count) return;
        var selected = list.Items[old]; list.Items.RemoveAt(old); list.Items.Insert(next, selected); list.SelectedIndex = next; list.ScrollIntoView(selected);
    }
    private async Task EntryNameAsync(bool copy)
    {
        if (SelectedWindows is not { } entry) return;
        var dialog = new NameDialog(this, copy ? "Copy Windows boot entry" : "Rename Windows boot entry", copy ? entry.Description + " (copy)" : entry.Description);
        if (dialog.ShowDialog() != true) return;
        await RunMutationAsync(new BootRequest { Operation = copy ? BootOperation.CopyEntry : BootOperation.RenameEntry, TargetId = entry.Id, Description = dialog.Value }, copy ? "Create a boot entry copy?" : "Rename this boot entry?", $"{entry.Description}\n→ {dialog.Value}" + (copy ? "\n\nThis copies the boot configuration. Windows files are not duplicated." : ""));
    }
    private async Task DeleteEntryAsync()
    {
        if (SelectedWindows is not { } entry) return;
        await RunMutationAsync(new BootRequest { Operation = BootOperation.DeleteEntry, TargetId = entry.Id }, "Delete this boot entry?", $"Remove {entry.Description} from Windows boot configuration. Its Windows files will remain on disk.", entry.Description);
    }
    private async Task SetOnceAsync(bool firmware, bool clear)
    {
        var id = firmware ? SelectedFirmware?.Id : (currentPage == "Windows boot" ? (menuOrder.SelectedItem as BootEntry)?.Id : SelectedWindows?.Id);
        var name = firmware ? SelectedFirmware?.Description : Snapshot.Entries.FirstOrDefault(x => x.Id == id)?.Description;
        if (!clear && id is null) { ShowError("Select a boot entry first."); return; }
        var operation = firmware ? clear ? BootOperation.ClearFirmwareBootOnce : BootOperation.SetFirmwareBootOnce : clear ? BootOperation.ClearBootOnce : BootOperation.SetBootOnce;
        await RunMutationAsync(new BootRequest { Operation = operation, TargetId = id ?? "" }, clear ? "Clear the next boot selection?" : "Use this entry for the next boot?", clear ? "The next start will follow the saved boot order." : $"Next boot: {name}\n\nThe saved default and boot order stay the same. This does not restart the PC.");
    }

    private async Task CreateBackupAsync()
    {
        var dialog = new NameDialog(this, "Create a backup", "Manual backup"); if (dialog.ShowDialog() != true) return;
        await RunMutationAsync(new BootRequest { Operation = BootOperation.CreateBackup, Description = dialog.Value }, "Create a boot backup?", "Save the current boot configuration as “" + dialog.Value + "”. Unapplied page changes are not included.", preserveDrafts: true);
    }
    private async Task RestoreBackupAsync()
    {
        if (SelectedBackup is not { } backup) return;
        await RunMutationAsync(new BootRequest { Operation = BootOperation.RestoreBackup, BackupId = backup.Id, Confirmation = "RESTORE" }, "Restore this boot backup?", $"{BackupLabel(backup)}\nCreated {backup.CreatedAt.LocalDateTime:f}\n\nThe saved boot configuration will replace the current settings. Review Diagnostics after restoring.", "RESTORE");
    }
    private async Task DeleteBackupAsync()
    {
        if (SelectedBackup is not { } backup) return;
        await RunMutationAsync(new BootRequest { Operation = BootOperation.DeleteBackup, BackupId = backup.Id }, "Delete this backup?", $"Permanently delete “{BackupLabel(backup)}”, created {backup.CreatedAt.LocalDateTime:f}.", preserveDrafts: true);
    }
    private async Task ExportAsync()
    {
        var dialog = new SaveFileDialog { Filter = "Uoo configuration (*.json)|*.json", FileName = "uoo-windows-configuration.json", Title = "Export current saved boot settings" };
        if (dialog.ShowDialog(this) != true) return;
        await RunMutationAsync(new BootRequest { Operation = BootOperation.ExportConfiguration, FilePath = dialog.FileName }, "Export current saved settings?", "Save Windows boot menu settings, entry names, and entry options to:\n" + dialog.FileName + "\n\nThis exports the current saved settings. Backups and unapplied page changes are not included.", preserveDrafts: true);
    }
    private async Task ImportAsync()
    {
        var dialog = new OpenFileDialog { Filter = "Uoo configuration (*.json)|*.json", Title = "Import Windows boot settings" };
        if (dialog.ShowDialog(this) != true) return;
        await RunMutationAsync(new BootRequest { Operation = BootOperation.ImportConfiguration, FilePath = dialog.FileName }, "Import boot settings?", "Import saved Windows boot menu settings, entry names, and entry options from:\n" + dialog.FileName + "\n\nOnly entries already present on this PC can be updated. The imported configuration is validated before it is applied.", "IMPORT");
    }
    private async Task AddFirmwareAsync()
    {
        var dialog = new EfiDialog(this, Snapshot); if (dialog.ShowDialog() != true) return;
        await RunMutationAsync(new BootRequest { Operation = BootOperation.AddFirmwareEntry, Description = dialog.EntryName, EfiPartition = dialog.Partition, EfiLoaderPath = dialog.Loader }, "Register this EFI loader?", $"Name: {dialog.EntryName}\nPartition: {dialog.Partition}\nLoader: {dialog.Loader}\n\nThe existing loader files stay in place.");
    }
    private async Task DeleteFirmwareAsync()
    {
        if (SelectedFirmware is not { } entry) return;
        await RunMutationAsync(new BootRequest { Operation = BootOperation.DeleteFirmwareEntry, TargetId = entry.Id }, "Delete this firmware entry?", $"Remove the firmware registration for {entry.Description}. The EFI loader file stays on disk.\n\nEntry ID: {entry.Id}", entry.Id);
    }
    private async Task RepairAsync()
    {
        var plan = Snapshot.Repair;
        await RunMutationAsync(new BootRequest { Operation = BootOperation.Repair, Confirmation = plan.Confirmation }, "Repair Windows boot files?", plan.Description + "\n\nWindows: " + plan.WindowsDirectory + "\nSystem partition: " + plan.SystemPartition + "\n\nBoot files and the Windows Boot Manager registration will be updated. The PC will not restart automatically.", plan.Confirmation);
    }
    private void ExportDiagnostics()
    {
        var dialog = new SaveFileDialog { Filter = "Text report (*.txt)|*.txt", FileName = "uoo-diagnostics.txt" }; if (dialog.ShowDialog(this) != true) return;
        try
        {
            var report = $"Uoo Boot Manager — {(demo ? "DEMO" : "LIVE")}\nGenerated {DateTimeOffset.Now:O}\n{Snapshot.Machine}\n{Snapshot.WindowsVersion}\n\n" + string.Join("\n\n", Snapshot.Diagnostics.Select(x => $"{x.Name}: {x.Status}\n{x.Detail}")) + "\n\n" + string.Join("\n", Snapshot.Errors);
            File.WriteAllText(dialog.FileName, report); StatusText.Text = "Diagnostics exported to " + dialog.FileName;
        }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException) { ShowError(error.Message); }
    }

    private static string BackupLabel(BackupInfo backup)
    {
        if (!backup.Description.StartsWith("Before ", StringComparison.Ordinal)) return backup.Description;
        return backup.Description[7..] switch
        {
            "SetMenu" => "Before boot menu changes", "SetEntryOptions" => "Before entry option changes",
            "RenameEntry" => "Before renaming an entry", "CopyEntry" => "Before copying an entry", "DeleteEntry" => "Before deleting an entry",
            "SetBootOnce" or "ClearBootOnce" => "Before changing the next Windows boot", "SetFirmwareOrder" => "Before firmware order changes",
            "SetFirmwareBootOnce" or "ClearFirmwareBootOnce" => "Before changing the next firmware boot",
            "AddFirmwareEntry" => "Before adding an EFI entry", "DeleteFirmwareEntry" => "Before deleting an EFI entry",
            "ImportConfiguration" => "Before importing settings", "RestoreBackup" => "Before restoring a backup", "Repair" => "Before boot repair", _ => backup.Description
        };
    }
}
