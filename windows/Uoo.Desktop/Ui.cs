using System.Windows;
using System.Windows.Automation;
using System.Windows.Controls;

namespace Uoo.Desktop;

internal static class Ui
{
    internal static TextBlock Text(string value, double size = 14, bool bold = false, Thickness? margin = null) => new()
    {
        Text = value, TextWrapping = TextWrapping.Wrap, FontSize = size,
        FontWeight = bold ? FontWeights.SemiBold : FontWeights.Normal, Margin = margin ?? new Thickness(0)
    };
    internal static Button Button(string name, Action action, bool primary = false)
    {
        var button = new Button { Content = name, Padding = new Thickness(14, 8, 14, 8), Margin = new Thickness(0, 0, 8, 6), MinHeight = 36 };
        AutomationProperties.SetName(button, name); button.Click += (_, _) => action();
        if (primary) button.SetResourceReference(FrameworkElement.StyleProperty, "AccentButtonStyle");
        return button;
    }
    internal static StackPanel Actions() => new() { Orientation = Orientation.Horizontal, Margin = new Thickness(0, 12, 0, 0) };
    internal static WrapPanel WrapActions() => new() { Margin = new Thickness(0, 12, 0, 0) };
    internal static Border Card(string title, params UIElement[] children)
    {
        var panel = new StackPanel();
        if (title.Length > 0) { var heading = new TextBlock { Text = title, TextWrapping = TextWrapping.Wrap }; heading.SetResourceReference(FrameworkElement.StyleProperty, "SectionTitle"); panel.Children.Add(heading); }
        foreach (var child in children) panel.Children.Add(child);
        var card = new Border { Child = panel }; card.SetResourceReference(FrameworkElement.StyleProperty, "Card"); return card;
    }
    internal static void Field(Panel parent, string label, Control control)
    {
        control.Margin = new Thickness(0, 0, 0, 12); control.MinHeight = 34;
        AutomationProperties.SetName(control, label);
        parent.Children.Add(new Label { Content = label, Target = control, Padding = new Thickness(0, 8, 0, 6) }); parent.Children.Add(control);
    }
    internal static StackPanel Page(string title, string description)
    {
        var panel = new StackPanel { MaxWidth = 1000, HorizontalAlignment = HorizontalAlignment.Stretch };
        var heading = new TextBlock { Text = title, TextWrapping = TextWrapping.Wrap }; heading.SetResourceReference(FrameworkElement.StyleProperty, "PageTitle"); panel.Children.Add(heading);
        panel.Children.Add(Text(description, margin: new Thickness(0, 0, 0, 22))); return panel;
    }
    internal static ScrollViewer Scroll(UIElement page) => new() { Content = page, VerticalScrollBarVisibility = ScrollBarVisibility.Auto, HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled, Padding = new Thickness(0, 0, 12, 12) };
    internal static void Name(DependencyObject control, string name) => AutomationProperties.SetName(control, name);
}
