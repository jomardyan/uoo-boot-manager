using System.Windows;
using System.Windows.Controls;
using Uoo.Core;

namespace Uoo.Desktop;

public partial class MainWindow
{
    private FrameworkElement BuildOverview()
    {
        var snapshot = Snapshot;
        var page = Ui.Page("Overview", "Manage Windows Boot Manager and UEFI firmware from one place.");
        var defaultEntry = snapshot.Entries.FirstOrDefault(x => x.Id == snapshot.Menu.DefaultId);
        page.Children.Add(Ui.Card(demo ? "Explore Uoo safely" : "This PC", Ui.Text(demo ? "All actions use sample data. You can change settings, create backups, and try a repair without changing this PC." : snapshot.Machine, 17, true), Ui.Text(snapshot.WindowsVersion, margin: new Thickness(0, 8, 0, 0))));
        var summary = new StackPanel();
        SummaryRow(summary, "Default Windows entry", defaultEntry?.Description ?? "Not available");
        SummaryRow(summary, "Boot menu", $"{snapshot.Menu.Timeout} seconds · {(snapshot.Menu.DisplayMenu ? "shown" : "automatic selection")}");
        SummaryRow(summary, "Next Windows boot", EntryName(snapshot.Menu.BootOnceId, false));
        SummaryRow(summary, "Firmware", snapshot.Firmware.Available ? $"UEFI · {snapshot.Firmware.Entries.Count} entries" : "Unavailable");
        SummaryRow(summary, "Saved backups", snapshot.Backups.Count.ToString());
        page.Children.Add(Ui.Card("Boot configuration", summary));
        var quick = Ui.WrapActions();
        quick.Children.Add(Ui.Button("Manage Windows boot", () => Navigate("Windows boot"), true));
        quick.Children.Add(Ui.Button("View diagnostics", () => Navigate("Diagnostics")));
        quick.Children.Add(Ui.Button("Backups and recovery", () => Navigate("Backups")));
        page.Children.Add(Ui.Card("Get started", quick));
        if (snapshot.RequiresAdministrator)
            page.Children.Add(Ui.Card("Administrator access needed", Ui.Text("Windows restricts access to boot configuration. Use Load as administrator above to inspect the complete configuration. Canceling approval keeps this window open.")));
        return Ui.Scroll(page);
    }

    private FrameworkElement BuildMenu()
    {
        var page = Ui.Page("Windows boot", "Choose the default entry and the order shown by Windows Boot Manager at startup.");
        var form = new StackPanel();
        defaultEntry = new ComboBox { ItemsSource = Snapshot.Entries.ToList(), SelectedValuePath = nameof(BootEntry.Id), SelectedValue = Snapshot.Menu.DefaultId };
        Ui.Field(form, "Default boot entry", defaultEntry);
        timeout = new TextBox { Text = Snapshot.Menu.Timeout.ToString(), MaxLength = 3, MaxWidth = 180, HorizontalAlignment = HorizontalAlignment.Left, MinWidth = 130 };
        Ui.Field(form, "Time to choose (0–300 seconds)", timeout);
        displayMenu = new CheckBox { Content = "Always show the Windows boot menu", IsChecked = Snapshot.Menu.DisplayMenu, Margin = new Thickness(0, 2, 0, 8) };
        form.Children.Add(displayMenu); page.Children.Add(Ui.Card("Startup settings", form));
        menuOrder = MakeList("Windows boot order", 165);
        var byId = Snapshot.Entries.ToDictionary(x => x.Id, StringComparer.OrdinalIgnoreCase);
        foreach (var id in Snapshot.Menu.Order) if (byId.TryGetValue(id, out var entry)) menuOrder.Items.Add(entry);
        menuOrder.SelectedIndex = menuOrder.Items.Count > 0 ? 0 : -1;
        var orderButtons = Ui.WrapActions();
        orderButtons.Children.Add(Ui.Button("Move up", () => Move(menuOrder, -1)));
        orderButtons.Children.Add(Ui.Button("Move down", () => Move(menuOrder, 1)));
        orderButtons.Children.Add(Ui.Button("Exclude selected", ExcludeMenuEntry));
        orderButtons.Children.Add(Ui.Button("Entry options", () =>
        {
            if (menuOrder.SelectedItem is BootEntry entry) SelectOptionsEntry(entry.Id);
            Navigate("Entry options");
        }));
        menuEntryPicker = new ComboBox { ItemsSource = Snapshot.Entries.ToList(), SelectedIndex = Snapshot.Entries.Count > 0 ? 0 : -1 };
        Ui.Name(menuEntryPicker, "Registered boot entry");
        var include = Ui.WrapActions();
        include.Children.Add(Ui.Button("Include in boot menu", IncludeMenuEntry));
        page.Children.Add(Ui.Card("Boot order", menuOrder, orderButtons,
            Ui.Text("Include or exclude registered entries without deleting them. Apply boot settings to save the menu."), menuEntryPicker, include));
        var once = Ui.WrapActions(); once.Children.Add(Ui.Button("Use selected entry once", async () => await SetOnceAsync(false, false)));
        var clear = Ui.Button("Clear next boot", async () => await SetOnceAsync(false, true)); clear.IsEnabled = Snapshot.Menu.BootOnceId.Length > 0; once.Children.Add(clear);
        page.Children.Add(Ui.Card("Next Windows boot", Ui.Text(EntryName(Snapshot.Menu.BootOnceId, false)), once));
        var apply = Ui.WrapActions(); apply.Children.Add(Ui.Button("Apply boot settings", async () => await ApplyMenuAsync(), true));
        apply.Children.Add(Ui.Button("Discard changes", () => DiscardCurrent("Windows boot")));
        page.Children.Add(apply); return Ui.Scroll(page);
    }

    private FrameworkElement BuildOptions()
    {
        var page = Ui.Page("Entry options", "Change a Windows boot entry. Advanced options apply to the selected installation.");
        entryPicker = new ComboBox { ItemsSource = Snapshot.Entries.Where(x => x.IsWindows).ToList(), SelectedValuePath = nameof(BootEntry.Id), SelectedValue = optionsEntryId };
        entryPicker.SelectionChanged += (_, _) =>
        {
            if (rendering || entryPicker.SelectedItem is not BootEntry selected || selected.Id == optionsEntryId) return;
            if (OptionsFingerprint() != optionsBaseline && !Confirm("Discard entry option changes?", "The unapplied options for the previous entry will be discarded.", null, false, out _))
            {
                rendering = true; entryPicker.SelectedValue = optionsEntryId; rendering = false; return;
            }
            optionsEntryId = selected.Id; FillOptions(selected.Options); optionsBaseline = OptionsFingerprint();
            if (entryActions is not null) UpdateEntryActions();
        };
        var selectedEntry = Snapshot.Entries.FirstOrDefault(x => x.Id == optionsEntryId);
        var entryCard = new StackPanel(); Ui.Field(entryCard, "Windows entry", entryPicker);
        var entryDetails = Ui.Text(selectedEntry is null ? "Load boot entries as administrator to manage their options." : $"{selectedEntry.Device} · {selectedEntry.Path}");
        entryCard.Children.Add(entryDetails);
        entryPicker.SelectionChanged += (_, _) => { if (entryPicker.SelectedItem is BootEntry entry) entryDetails.Text = $"{entry.Device} · {entry.Path}"; };
        entryActions = Ui.WrapActions();
        renameEntry = Ui.Button("Rename", async () => await EntryNameAsync(false));
        copyEntry = Ui.Button("Copy entry", async () => await EntryNameAsync(true));
        deleteEntry = Ui.Button("Delete entry", async () => await DeleteEntryAsync());
        entryActions.Children.Add(renameEntry); entryActions.Children.Add(copyEntry); entryActions.Children.Add(deleteEntry);
        entryActions.Children.Add(Ui.Button("Use for next boot", async () => await SetOnceAsync(false, false)));
        entryCard.Children.Add(entryActions); page.Children.Add(Ui.Card("Selected entry", entryCard));
        safeBoot = new ComboBox { ItemsSource = new[] { "Off", "Minimal", "Network", "AlternateShell" } };
        menuPolicy = new ComboBox { ItemsSource = new[] { "Standard", "Legacy" } };
        bootLog = new CheckBox { Content = "Write a boot log (ntbtlog.txt)", Margin = new Thickness(0, 6, 0, 8) };
        sos = new CheckBox { Content = "Show driver names while starting", Margin = new Thickness(0, 6, 0, 8) };
        baseVideo = new CheckBox { Content = "Use basic video", Margin = new Thickness(0, 6, 0, 8) };
        recovery = new CheckBox { Content = "Enable Windows recovery", Margin = new Thickness(0, 6, 0, 8) };
        FillOptions(selectedEntry?.Options ?? new BootEntryOptions());
        var options = new StackPanel(); Ui.Field(options, "Safe mode", safeBoot);
        options.Children.Add(Ui.Text("Minimal starts basic Windows services. Network also enables networking. AlternateShell starts a command prompt.", margin: new Thickness(0, 0, 0, 12)));
        options.Children.Add(bootLog); options.Children.Add(sos); options.Children.Add(baseVideo); options.Children.Add(recovery);
        Ui.Field(options, "Boot menu style", menuPolicy); page.Children.Add(Ui.Card("Advanced startup", options));
        var apply = Ui.WrapActions(); var applyButton = Ui.Button("Apply entry options", async () => await ApplyOptionsAsync(), true); applyButton.IsEnabled = selectedEntry is not null;
        apply.Children.Add(applyButton); apply.Children.Add(Ui.Button("Discard changes", () => DiscardCurrent("Entry options"))); page.Children.Add(apply);
        UpdateEntryActions(); return Ui.Scroll(page);
    }
    private WrapPanel? entryActions;
    private Button renameEntry = new(), copyEntry = new(), deleteEntry = new();
    private void UpdateEntryActions()
    {
        var entry = SelectedWindows; renameEntry.IsEnabled = entry is not null; copyEntry.IsEnabled = entry is not null;
        deleteEntry.IsEnabled = entry is not null && !entry.IsCurrent && !entry.IsDefault && Snapshot.Menu.BootOnceId != entry.Id && Snapshot.Entries.Count(x => x.IsWindows) > 1;
        deleteEntry.ToolTip = deleteEntry.IsEnabled ? "Remove this entry from the boot configuration" : "The current, default, scheduled, or last Windows entry is protected.";
    }
    private void FillOptions(BootEntryOptions options)
    {
        safeBoot.SelectedItem = options.SafeBoot; menuPolicy.SelectedItem = options.MenuPolicy;
        bootLog.IsChecked = options.BootLog; sos.IsChecked = options.Sos; baseVideo.IsChecked = options.BaseVideo; recovery.IsChecked = options.RecoveryEnabled;
    }
    private void SelectOptionsEntry(string id)
    {
        if (entryPicker.Items.OfType<BootEntry>().Any(x => x.Id == id)) entryPicker.SelectedValue = id;
    }

    private FrameworkElement BuildFirmware()
    {
        var firmware = Snapshot.Firmware;
        var page = Ui.Page("Firmware", "Manage the UEFI boot order and existing EFI loaders.");
        firmwareOrder = MakeList("UEFI firmware boot order", 210);
        var byId = firmware.Entries.ToDictionary(x => x.Id, StringComparer.OrdinalIgnoreCase);
        foreach (var id in firmware.Order)
            if (byId.TryGetValue(id, out var entry)) firmwareOrder.Items.Add(entry);
        firmwareOrder.SelectedIndex = firmwareOrder.Items.Count > 0 ? 0 : -1;
        if (!firmware.Available) page.Children.Add(Ui.Card("UEFI is unavailable", Ui.Text(firmware.Reason.Length > 0 ? firmware.Reason : "This PC is using legacy BIOS, or Windows could not read its firmware entries.")));
        var orderActions = Ui.WrapActions();
        orderActions.Children.Add(Ui.Button("Move up", () => Move(firmwareOrder, -1))); orderActions.Children.Add(Ui.Button("Move down", () => Move(firmwareOrder, 1)));
        var exclude = Ui.Button("Exclude selected", () =>
        {
            if (firmwareOrder.SelectedItem is not FirmwareEntry entry) return;
            if (firmware.CurrentId.Length == 0 && firmware.Order.Contains(entry.Id, StringComparer.OrdinalIgnoreCase)) { ShowError("The current firmware entry could not be identified. Keep all saved order entries; reordering and additions are still available."); return; }
            if (entry.IsCurrent || firmware.CurrentId == entry.Id || firmware.BootOnceId == entry.Id) { ShowError("Keep the current and scheduled firmware entries in the boot order."); return; }
            if (firmwareOrder.Items.Count <= 1) { ShowError("Keep at least one entry in the firmware boot order."); return; }
            firmwareOrder.Items.Remove(entry);
        }); orderActions.Children.Add(exclude);
        void UpdateExclusion()
        {
            var entry = firmwareOrder.SelectedItem as FirmwareEntry;
            exclude.IsEnabled = entry is not null && firmwareOrder.Items.Count > 1 && !entry.IsCurrent && firmware.CurrentId != entry.Id && firmware.BootOnceId != entry.Id &&
                (firmware.CurrentId.Length != 0 || !firmware.Order.Contains(entry.Id, StringComparer.OrdinalIgnoreCase));
            exclude.ToolTip = firmware.CurrentId.Length == 0
                ? "The current firmware entry could not be identified; keep all saved order entries."
                : "The current, scheduled, and last order entries are protected.";
        }
        firmwareOrder.SelectionChanged += (_, _) => UpdateExclusion(); UpdateExclusion();
        orderActions.Children.Add(Ui.Button("Apply firmware order", async () => await ApplyFirmwareOrderAsync(), true));
        orderActions.Children.Add(Ui.Button("Discard changes", () => DiscardCurrent("Firmware")));
        var orderCard = Ui.Card("Firmware boot order", firmwareOrder, orderActions); orderCard.IsEnabled = firmware.Available; page.Children.Add(orderCard);
        firmwarePicker = new ComboBox { ItemsSource = firmware.Entries.ToList(), SelectedIndex = firmware.Entries.Count > 0 ? 0 : -1, Margin = new Thickness(0, 6, 0, 8) };
        Ui.Name(firmwarePicker, "Registered EFI entry");
        firmwareOrder.SelectionChanged += (_, _) => { if (firmwareOrder.SelectedItem is FirmwareEntry entry) firmwarePicker.SelectedItem = entry; };
        var include = Ui.Button("Include in boot order", () =>
        {
            if (SelectedFirmware is not { } entry) return;
            if (firmwareOrder.Items.OfType<FirmwareEntry>().Any(x => x.Id == entry.Id)) { firmwareOrder.SelectedItem = entry; return; }
            firmwareOrder.Items.Add(entry); firmwareOrder.SelectedItem = entry;
        });
        var once = Ui.WrapActions(); once.Children.Add(Ui.Button("Use selected entry once", async () => await SetOnceAsync(true, false)));
        var clear = Ui.Button("Clear next boot", async () => await SetOnceAsync(true, true)); clear.IsEnabled = firmware.BootOnceId.Length > 0; once.Children.Add(clear);
        var onceCard = Ui.Card("Next firmware boot", Ui.Text(EntryName(firmware.BootOnceId, true)), once); onceCard.IsEnabled = firmware.Available;
        var manage = Ui.WrapActions(); manage.Children.Add(include); manage.Children.Add(Ui.Button("Add an EFI loader…", async () => await AddFirmwareAsync()));
        var delete = Ui.Button("Delete selected entry", async () => await DeleteFirmwareAsync()); manage.Children.Add(delete);
        var detail = Ui.Text("");
        void Update()
        {
            var entry = SelectedFirmware;
            detail.Text = entry is null ? "Select an entry to see its loader." : $"{entry.Device}\n{entry.Path}\n{entry.Id}";
            delete.IsEnabled = entry is not null && firmware.CurrentId.Length != 0 && !entry.IsWindowsBootManager && !entry.IsCurrent && firmware.CurrentId != entry.Id && firmware.Entries.Count > 1 && firmware.BootOnceId != entry.Id;
            delete.ToolTip = firmware.CurrentId.Length == 0 ? "The current firmware entry could not be identified; removing registrations is unavailable." : delete.IsEnabled ? "Remove this EFI registration; its loader file remains." : "Windows Boot Manager, scheduled entries, and the last firmware entry are protected.";
        }
        firmwarePicker.SelectionChanged += (_, _) => Update(); Update();
        var manageCard = Ui.Card("Registered EFI entries", Ui.Text("Choose any registered loader, including entries excluded from the saved boot order. Including or excluding an entry takes effect after Apply firmware order. Windows Boot Manager is protected from deletion."), firmwarePicker, detail, manage); manageCard.IsEnabled = firmware.Available; page.Children.Add(manageCard); page.Children.Add(onceCard);
        return Ui.Scroll(page);
    }

    private FrameworkElement BuildBackups()
    {
        var selectedId = SelectedBackup?.Id;
        var page = Ui.Page("Backups", "Save boot configuration before changes and restore a previous state when needed.");
        backups = MakeList("Boot configuration backups", 220);
        foreach (var backup in Snapshot.Backups.OrderByDescending(x => x.CreatedAt))
        {
            var item = new ListBoxItem { Tag = backup, Padding = new Thickness(10), HorizontalContentAlignment = HorizontalAlignment.Stretch };
            var content = new StackPanel(); content.Children.Add(Ui.Text(BackupLabel(backup), bold: true));
            content.Children.Add(Ui.Text($"{backup.CreatedAt.LocalDateTime:g} · {(backup.CanRestore ? "Ready to restore" : "Cannot restore")}", 12, margin: new Thickness(0, 4, 0, 0)));
            item.Content = content; Ui.Name(item, BackupLabel(backup)); backups.Items.Add(item);
            if (backup.Id == selectedId) backups.SelectedItem = item;
        }
        if (backups.SelectedIndex < 0 && backups.Items.Count > 0) backups.SelectedIndex = 0;
        backupDetail = Ui.Text("");
        var actions = Ui.WrapActions(); actions.Children.Add(Ui.Button("Create backup…", async () => await CreateBackupAsync(), true));
        restoreBackup = Ui.Button("Restore…", async () => await RestoreBackupAsync()); deleteBackup = Ui.Button("Delete…", async () => await DeleteBackupAsync());
        actions.Children.Add(restoreBackup); actions.Children.Add(deleteBackup);
        void Update()
        {
            var backup = SelectedBackup; restoreBackup.IsEnabled = backup?.CanRestore == true; deleteBackup.IsEnabled = backup is not null;
            backupDetail.Text = backup is null ? "No backups yet. A backup is created automatically before boot settings change." : $"{backup.Detail}\nBackup ID: {backup.Id}";
            restoreBackup.ToolTip = backup?.CanRestore == false ? backup.Detail : "Review and confirm restoration of this backup.";
        }
        backups.SelectionChanged += (_, _) => Update(); Update();
        page.Children.Add(Ui.Card("Saved backups", backups, backupDetail, actions));
        var transfer = Ui.WrapActions(); transfer.Children.Add(Ui.Button("Export current settings…", async () => await ExportAsync())); transfer.Children.Add(Ui.Button("Import settings…", async () => await ImportAsync()));
        page.Children.Add(Ui.Card("Configuration files", Ui.Text("Export saved Windows boot menu settings, entry names, and entry options. Import applies only to matching entries already on this PC. Firmware registrations and backup files are managed separately."), transfer));
        return Ui.Scroll(page);
    }

    private FrameworkElement BuildDiagnostics()
    {
        var page = Ui.Page("Diagnostics", "Inspect the system and boot environment before making changes.");
        var machine = new StackPanel(); SummaryRow(machine, "Computer", Snapshot.Machine); SummaryRow(machine, "Windows", Snapshot.WindowsVersion);
        SummaryRow(machine, "Mode", demo ? "Demo · sample data" : "Live · this PC");
        SummaryRow(machine, "Access", Snapshot.RequiresAdministrator ? "Administrator access needed for full details" : "Boot configuration available");
        page.Children.Add(Ui.Card("System", machine));
        foreach (var check in Snapshot.Diagnostics)
        {
            var detail = new StackPanel(); detail.Children.Add(Ui.Text(check.Status, bold: true)); detail.Children.Add(Ui.Text(check.Detail, margin: new Thickness(0, 6, 0, 0)));
            page.Children.Add(Ui.Card(check.Name, detail));
        }
        if (Snapshot.Diagnostics.Count == 0) page.Children.Add(Ui.Card("No checks available", Ui.Text("Load with administrator access or refresh to read this PC's boot environment.")));
        if (Snapshot.Errors.Count > 0) page.Children.Add(Ui.Card("Items to review", Ui.Text(string.Join("\n\n", Snapshot.Errors))));
        var actions = Ui.WrapActions(); actions.Children.Add(Ui.Button("Refresh diagnostics", async () => await RefreshAsync())); actions.Children.Add(Ui.Button("Export report…", ExportDiagnostics)); page.Children.Add(actions);
        return Ui.Scroll(page);
    }

    private FrameworkElement BuildRepair()
    {
        var plan = Snapshot.Repair;
        var page = Ui.Page("Repair", "Review a guided Windows boot repair plan before changing boot files.");
        page.Children.Add(Ui.Card("Before you begin", Ui.Text("Keep a current backup and your BitLocker recovery key available. This repair works on the Windows installation and system partition identified below. It does not restart this PC.")));
        var details = new StackPanel();
        if (plan.Available)
        {
            details.Children.Add(Ui.Text(plan.Description, margin: new Thickness(0, 0, 0, 14)));
            SummaryRow(details, "Windows directory", plan.WindowsDirectory); SummaryRow(details, "System partition", plan.SystemPartition);
        }
        else details.Children.Add(Ui.Text(plan.Reason.Length > 0 ? plan.Reason : "A supported repair target could not be identified. Load as administrator and check Diagnostics."));
        var actions = Ui.WrapActions(); actions.Children.Add(Ui.Button("Refresh repair plan", async () => await RefreshAsync()));
        var repair = Ui.Button("Review and repair…", async () => await RepairAsync(), true); repair.IsEnabled = plan.Available; actions.Children.Add(repair);
        details.Children.Add(actions); page.Children.Add(Ui.Card(plan.Available ? "Proposed repair" : "Repair unavailable", details));
        var links = Ui.WrapActions(); links.Children.Add(Ui.Button("Open backups", () => Navigate("Backups"))); links.Children.Add(Ui.Button("Open diagnostics", () => Navigate("Diagnostics"))); page.Children.Add(links);
        return Ui.Scroll(page);
    }

    private void DiscardCurrent(string page)
    {
        var changed = page switch { "Windows boot" => MenuFingerprint() != menuBaseline, "Entry options" => OptionsFingerprint() != optionsBaseline, "Firmware" => FirmwareFingerprint() != firmwareBaseline, _ => false };
        if (changed && !Confirm("Discard these changes?", "The unapplied changes on this page will be replaced with the saved settings.", null, false, out _)) return;
        rendering = true;
        try
        {
            pages[page] = page switch { "Windows boot" => BuildMenu(), "Entry options" => BuildOptions(), "Firmware" => BuildFirmware(), _ => pages[page] };
            if (page == "Windows boot") menuBaseline = MenuFingerprint();
            else if (page == "Entry options") optionsBaseline = OptionsFingerprint(); else if (page == "Firmware") firmwareBaseline = FirmwareFingerprint();
            if (currentPage == page) PageHost.Content = pages[page];
        }
        finally { rendering = false; }
    }
    private string EntryName(string id, bool firmware) => id.Length == 0 ? "Use the saved default and boot order" : firmware ? Snapshot.Firmware.Entries.FirstOrDefault(x => x.Id == id)?.Description ?? id : Snapshot.Entries.FirstOrDefault(x => x.Id == id)?.Description ?? id;
    private static ListBox MakeList(string name, double height)
    {
        var list = new ListBox { Height = height, HorizontalContentAlignment = HorizontalAlignment.Stretch };
        ScrollViewer.SetHorizontalScrollBarVisibility(list, ScrollBarVisibility.Disabled); Ui.Name(list, name); return list;
    }
    private static void SummaryRow(Panel parent, string label, string value)
    {
        var row = new Grid { Margin = new Thickness(0, 5, 0, 5) }; row.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(180) }); row.ColumnDefinitions.Add(new ColumnDefinition());
        var name = Ui.Text(label); name.Opacity = .7; row.Children.Add(name);
        var text = Ui.Text(value); Grid.SetColumn(text, 1); row.Children.Add(text); parent.Children.Add(row);
    }
}
