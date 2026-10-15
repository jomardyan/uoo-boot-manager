using System.Windows;
using System.Windows.Automation;
using System.Windows.Controls;
using Microsoft.Win32;
using Uoo.Core;

namespace Uoo.Desktop;

internal sealed class ConfirmDialog : Window
{
    internal TextBox ConfirmationBox { get; } = new();
    internal CheckBox RecoveryKeyBox { get; } = new() { Content = "I have access to my BitLocker recovery key", Margin = new Thickness(0, 12, 0, 0) };
    internal Button ConfirmButton { get; } = new() { Content = "Apply", MinWidth = 96, IsDefault = true };
    internal bool RecoveryKeyAcknowledged => RecoveryKeyBox.IsChecked == true;

    internal ConfirmDialog(Window owner, string title, string summary, string? required = null, bool recoveryKey = false)
    {
        Owner = owner; Title = title; ThemeMode = owner.ThemeMode;
        Width = 530; SizeToContent = SizeToContent.Height; MaxHeight = 680;
        ResizeMode = ResizeMode.NoResize; WindowStartupLocation = WindowStartupLocation.CenterOwner;
        var layout = new Grid { Margin = new Thickness(24) };
        layout.RowDefinitions.Add(new RowDefinition());
        layout.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        var body = new StackPanel();
        body.Children.Add(Ui.Text(title, 22, true));
        body.Children.Add(Ui.Text(summary, margin: new Thickness(0, 14, 0, 8)));
        if (required is not null)
        {
            body.Children.Add(Ui.Text($"Type {required} to confirm:", margin: new Thickness(0, 8, 0, 8)));
            AutomationProperties.SetName(ConfirmationBox, "Confirmation text");
            body.Children.Add(ConfirmationBox);
            ConfirmationBox.TextChanged += (_, _) => Update();
        }
        if (recoveryKey)
        {
            body.Children.Add(Ui.Text("BitLocker protection is enabled or its status could not be read. Boot changes may require your recovery key at the next start.", margin: new Thickness(0, 12, 0, 0)));
            body.Children.Add(RecoveryKeyBox);
            RecoveryKeyBox.Checked += (_, _) => Update(); RecoveryKeyBox.Unchecked += (_, _) => Update();
        }
        var scroll = new ScrollViewer { Content = body, VerticalScrollBarVisibility = ScrollBarVisibility.Auto, MaxHeight = 490 };
        layout.Children.Add(scroll);
        var actions = new StackPanel { Orientation = Orientation.Horizontal, HorizontalAlignment = HorizontalAlignment.Right, Margin = new Thickness(0, 20, 0, 0) };
        var cancel = new Button { Content = "Cancel", MinWidth = 96, IsCancel = true, Margin = new Thickness(0, 0, 8, 0) };
        actions.Children.Add(cancel); actions.Children.Add(ConfirmButton);
        ConfirmButton.SetResourceReference(StyleProperty, "AccentButtonStyle");
        ConfirmButton.Click += (_, _) => DialogResult = true;
        Grid.SetRow(actions, 1); layout.Children.Add(actions); Content = layout;
        Update();
        Loaded += (_, _) => { if (required is not null) ConfirmationBox.Focus(); else cancel.Focus(); };
        void Update() => ConfirmButton.IsEnabled = (required is null || ConfirmationBox.Text == required) && (!recoveryKey || RecoveryKeyBox.IsChecked == true);
    }
}

internal sealed class NameDialog : Window
{
    internal string Value => _entry.Text.Trim();
    private readonly TextBox _entry = new() { MaxLength = 120 };
    internal NameDialog(Window owner, string title, string initial)
    {
        Owner = owner; Title = title; ThemeMode = owner.ThemeMode; Width = 490; SizeToContent = SizeToContent.Height;
        ResizeMode = ResizeMode.NoResize; WindowStartupLocation = WindowStartupLocation.CenterOwner;
        var panel = new StackPanel { Margin = new Thickness(24) };
        panel.Children.Add(Ui.Text(title, 22, true)); panel.Children.Add(Ui.Text("Name", margin: new Thickness(0, 18, 0, 8)));
        _entry.Text = initial; AutomationProperties.SetName(_entry, "Name"); panel.Children.Add(_entry);
        var error = Ui.Text("", margin: new Thickness(0, 8, 0, 0)); panel.Children.Add(error);
        var actions = new StackPanel { Orientation = Orientation.Horizontal, HorizontalAlignment = HorizontalAlignment.Right, Margin = new Thickness(0, 18, 0, 0) };
        actions.Children.Add(new Button { Content = "Cancel", IsCancel = true, MinWidth = 90, Margin = new Thickness(0, 0, 8, 0) });
        var save = new Button { Content = "Continue", IsDefault = true, MinWidth = 100 }; save.SetResourceReference(StyleProperty, "AccentButtonStyle");
        save.Click += (_, _) => { try { BootValidation.Description(Value); DialogResult = true; } catch (ArgumentException ex) { error.Text = ex.Message; } };
        actions.Children.Add(save); panel.Children.Add(actions); Content = panel;
        Loaded += (_, _) => { _entry.Focus(); _entry.SelectAll(); };
    }
}

internal sealed class EfiDialog : Window
{
    private readonly TextBox _name = new() { MaxLength = 120 };
    private readonly TextBox _partition = new();
    private readonly TextBox _loader = new();
    internal string EntryName => _name.Text.Trim();
    internal string Partition => _partition.Text.Trim();
    internal string Loader => _loader.Text.Trim();
    internal EfiDialog(Window owner, BootSnapshot snapshot)
    {
        Owner = owner; Title = "Add an EFI boot entry"; ThemeMode = owner.ThemeMode; Width = 570;
        SizeToContent = SizeToContent.Height; ResizeMode = ResizeMode.NoResize; WindowStartupLocation = WindowStartupLocation.CenterOwner;
        var panel = new StackPanel { Margin = new Thickness(24) };
        panel.Children.Add(Ui.Text(Title, 22, true));
        panel.Children.Add(Ui.Text("Register an existing loader on a mounted EFI system partition. The loader is checked again before it is registered.", margin: new Thickness(0, 12, 0, 8)));
        _partition.Text = snapshot.Repair.SystemPartition;
        _loader.Text = System.Runtime.InteropServices.RuntimeInformation.OSArchitecture == System.Runtime.InteropServices.Architecture.Arm64
            ? @"\EFI\ubuntu\shimaa64.efi" : @"\EFI\ubuntu\shimx64.efi";
        Ui.Field(panel, "Entry name", _name); Ui.Field(panel, @"EFI partition (for example S:\)", _partition); Ui.Field(panel, @"Loader path (\EFI\… .efi)", _loader);
        var browse = Ui.Button("Browse for an EFI loader…", () =>
        {
            var dialog = new OpenFileDialog { Filter = "EFI loaders (*.efi)|*.efi", Title = "Choose an existing EFI loader" };
            if (dialog.ShowDialog(this) != true) return;
            var root = System.IO.Path.GetPathRoot(dialog.FileName) ?? "";
            _partition.Text = root; _loader.Text = "\\" + dialog.FileName[root.Length..].Replace('/', '\\');
        }); panel.Children.Add(browse);
        var error = Ui.Text("", margin: new Thickness(0, 8, 0, 0)); panel.Children.Add(error);
        var buttons = Ui.Actions(); buttons.Children.Add(new Button { Content = "Cancel", IsCancel = true, MinWidth = 90, Margin = new Thickness(0, 0, 8, 0) });
        var add = Ui.Button("Continue", () =>
        {
            try
            {
                BootValidation.Validate(new BootRequest { Operation = BootOperation.AddFirmwareEntry, ExpectedRevision = snapshot.Revision, Description = EntryName, EfiPartition = Partition, EfiLoaderPath = Loader }, snapshot);
                DialogResult = true;
            }
            catch (ArgumentException ex) { error.Text = ex.Message; }
        }, true);
        buttons.Children.Add(add); panel.Children.Add(buttons); Content = panel;
    }
}
