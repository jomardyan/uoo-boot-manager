"""Native GTK4/libadwaita building blocks (Ubuntu 24.04 or newer)."""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, Gtk


def text(value, css=None, wrap=False):
    label = Gtk.Label(label=str(value), xalign=0, wrap=wrap)
    if css:
        label.add_css_class(css)
    return label


def row(title, subtitle="", icon=None):
    widget = Adw.ActionRow(title=str(title), subtitle=str(subtitle))
    widget.set_use_markup(False)
    widget.set_title_lines(2)
    widget.set_subtitle_lines(2)
    if icon:
        widget.add_prefix(Gtk.Image.new_from_icon_name(icon))
    return widget


def group(title=None, description=None):
    widget = Adw.PreferencesGroup()
    if title:
        widget.set_title(title)
    if description:
        widget.set_description(description)
    return widget


def entry(title, value=""):
    widget = Adw.EntryRow(title=title)
    widget.set_use_markup(False)
    widget.set_text(str(value))
    return widget


def combo(title, choices, selected=0):
    widget = Adw.ComboRow(title=title, model=Gtk.StringList.new(list(choices)))
    widget.set_use_markup(False)
    widget.set_selected(selected)
    widget._choices = list(choices)
    return widget


def combo_value(widget):
    selected = widget.get_selected()
    return widget._choices[selected] if selected < len(widget._choices) else ""


def set_combo(widget, value):
    if value in widget._choices:
        widget.set_selected(widget._choices.index(value))


def switch(title, active=False, subtitle=None):
    widget = Adw.SwitchRow(title=title, active=active)
    widget.set_use_markup(False)
    if subtitle:
        widget.set_subtitle(subtitle)
    return widget


def spin(title, value=10):
    widget = row(title)
    control = Gtk.SpinButton.new_with_range(-1, 300, 1)
    control.set_value(value)
    control.set_numeric(True)
    control.set_width_chars(5)
    control.set_valign(Gtk.Align.CENTER)
    widget.add_suffix(control)
    widget.set_activatable_widget(control)
    return widget, control


def button(label, callback, icon=None, suggested=False, destructive=False):
    widget = Gtk.Button(label=label) if label else Gtk.Button.new_from_icon_name(icon)
    if label and icon:
        contents = Gtk.Box(spacing=8)
        contents.append(Gtk.Image.new_from_icon_name(icon))
        contents.append(Gtk.Label(label=label))
        widget.set_child(contents)
    if suggested:
        widget.add_css_class("suggested-action")
    if destructive:
        widget.add_css_class("destructive-action")
    if not label:
        name = {"go-up-symbolic": "Move up", "go-down-symbolic": "Move down", "view-refresh-symbolic": "Reload",
                "document-open-symbolic": "Browse for a file", "changes-prevent-symbolic": "Administrator access"}.get(icon, "Open actions")
        widget.set_tooltip_text(name)
        widget.update_property([Gtk.AccessibleProperty.LABEL], [name])
    widget.connect("clicked", lambda _button: callback())
    return widget


def menu(label, commands):
    widget = Gtk.MenuButton(label=label)
    popover = Gtk.Popover()
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4,
                  margin_top=8, margin_bottom=8, margin_start=8, margin_end=8)
    for title, callback in commands:
        child = Gtk.Button(label=title)
        child.add_css_class("flat")

        def activate(_button, action=callback):
            popover.popdown()
            action()

        child.connect("clicked", activate)
        box.append(child)
    popover.set_child(box)
    widget.set_popover(popover)
    return widget


class Page(Gtk.Box):
    def __init__(self, title, description):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=16,
                         margin_top=22, margin_bottom=18, margin_start=24, margin_end=24)
        header = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        header.append(text(title, "title-1"))
        header.append(text(description, "dim-label", True))
        self.append(header)
        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18,
                            margin_bottom=6)
        clamp = Adw.Clamp(maximum_size=850, tightening_threshold=600, child=self.body)
        self.scroll = Gtk.ScrolledWindow(vexpand=True, hexpand=True,
                                        hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.scroll.set_child(clamp)
        self.append(self.scroll)
        self.footer = Gtk.Box(spacing=8)
        self.append(self.footer)


def listbox():
    widget = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
    widget.add_css_class("boxed-list")
    return widget


def clear_list(widget):
    child = widget.get_first_child()
    while child:
        following = child.get_next_sibling()
        widget.remove(child)
        child = following


def editor_window(parent, title):
    window = Adw.Window(transient_for=parent, modal=True, default_width=650, default_height=600)
    shell = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    header = Adw.HeaderBar()
    header.set_title_widget(Adw.WindowTitle(title=title))
    shell.append(header)
    window.set_content(shell)
    return window, shell


def file_dialog(parent, title, save, callback, pattern=None, name=None, folder=None):
    chooser = Gtk.FileChooserNative.new(title, parent,
                                        Gtk.FileChooserAction.SAVE if save else Gtk.FileChooserAction.OPEN,
                                        "Save" if save else "Open", "Cancel")
    if name:
        chooser.set_current_name(name)
    if folder:
        chooser.set_current_folder(Gio.File.new_for_path(folder))
    if pattern:
        filtering = Gtk.FileFilter()
        filtering.set_name(pattern)
        filtering.add_pattern(pattern)
        chooser.add_filter(filtering)

    def response(dialog, response_id):
        if response_id == Gtk.ResponseType.ACCEPT and dialog.get_file():
            callback(dialog.get_file().get_path())
        dialog.destroy()

    chooser.connect("response", response)
    chooser.show()
    return chooser
