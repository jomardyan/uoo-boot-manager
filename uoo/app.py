"""Native Ubuntu desktop interface for Uoo Boot Manager."""

import argparse
import copy
from datetime import datetime
import json
from pathlib import Path
import queue
import subprocess
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from . import system


HELPER = Path("/usr/lib/uoo-boot-manager/helper.py")
BG = "#17171e"
PANEL = "#23232e"
FIELD = "#30303e"
TEXT = "#f4f2f6"
MUTED = "#b8b4c5"
ORANGE = "#e95420"
STYLES = {"Show menu": "menu", "Hidden": "hidden", "Countdown": "countdown"}


def trusted_helper_available():
    """Privilege elevation is only available for the installed helper."""
    try:
        metadata = HELPER.lstat()
        return (HELPER.is_file() and not HELPER.is_symlink()
                and metadata.st_uid == 0 and not metadata.st_mode & 0o022
                and Path("/usr/bin/pkexec").is_file())
    except OSError:
        return False


def run_helper(request):
    result = subprocess.run(
        ["/usr/bin/pkexec", "/usr/bin/python3", "-I", str(HELPER), json.dumps(request)],
        capture_output=True, text=True, timeout=600,
    )
    try:
        response = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError) as exc:
        if result.returncode in (126, 127):
            raise RuntimeError("Administrator authentication was cancelled or unavailable.") from exc
        raise RuntimeError(result.stderr.strip() or "The administrator helper did not return a valid response.") from exc
    if not isinstance(response, dict) or not response.get("ok"):
        raise RuntimeError(response.get("error", "The requested change could not be completed.")
                           if isinstance(response, dict) else "Invalid helper response.")
    if result.returncode:
        raise RuntimeError(response.get("error", "The administrator helper failed."))
    return response


class App:
    def __init__(self, root, demo=False, auto_demo=True):
        self.root = root
        self.explicit_demo = demo
        self.auto_demo = auto_demo
        self.demo = demo
        self.snapshot = {}
        self.backups = []
        self.demo_states = {}
        self.demo_counter = 0
        self.order = []
        self.busy = False
        self.closed = False
        self.events = queue.Queue()
        self.installed = trusted_helper_available()
        self.action_buttons = []
        self.edit_widgets = []
        self.root.title("Uoo Boot Manager")
        self.root.geometry("1120x770")
        self.root.minsize(900, 660)
        self.root.configure(background=BG)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.bind("<Control-r>", lambda _event: self.reload())
        self._theme()
        self._build()
        self.root.after(80, self._poll)
        self.reload()

    def _theme(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(".", background=BG, foreground=TEXT, font=("Ubuntu", 11))
        style.configure("TFrame", background=BG)
        style.configure("Card.TFrame", background=PANEL)
        style.configure("TLabel", background=BG, foreground=TEXT)
        style.configure("Muted.TLabel", foreground=MUTED)
        style.configure("Card.TLabel", background=PANEL)
        style.configure("CardMuted.TLabel", background=PANEL, foreground=MUTED)
        style.configure("Title.TLabel", font=("Ubuntu", 24, "bold"))
        style.configure("Section.TLabel", font=("Ubuntu", 15, "bold"))
        style.configure("TNotebook", background=BG, borderwidth=0,
                        bordercolor=BG, lightcolor=BG, darkcolor=BG)
        style.configure("TNotebook.Tab", background=PANEL, foreground=MUTED,
                        padding=(22, 12), font=("Ubuntu", 11, "bold"),
                        bordercolor=BG, lightcolor=BG, darkcolor=BG)
        style.map("TNotebook.Tab", background=[("selected", FIELD)], foreground=[("selected", TEXT)])
        style.configure("Treeview", background=PANEL, fieldbackground=PANEL,
                        foreground=TEXT, borderwidth=0, rowheight=38,
                        bordercolor=PANEL, lightcolor=PANEL, darkcolor=PANEL)
        style.configure("Treeview.Heading", background=FIELD, foreground=MUTED,
                        relief="flat", padding=(10, 10), font=("Ubuntu", 10, "bold"))
        style.map("Treeview", background=[("selected", "#59332d")], foreground=[("selected", TEXT)])
        style.map("Treeview.Heading", background=[("active", FIELD)])
        style.configure("TButton", background=FIELD, foreground=TEXT, borderwidth=0,
                        padding=(15, 10), font=("Ubuntu", 10, "bold"))
        style.map("TButton", background=[("disabled", PANEL), ("active", "#434353")],
                  foreground=[("disabled", "#827e90")])
        style.configure("Accent.TButton", background=ORANGE)
        style.map("Accent.TButton", background=[("disabled", FIELD), ("active", "#fa6937")])
        style.configure("TCombobox", fieldbackground=FIELD, background=FIELD,
                        foreground=TEXT, arrowcolor=TEXT, padding=7)
        style.map("TCombobox", fieldbackground=[("readonly", FIELD)],
                  foreground=[("readonly", TEXT), ("disabled", MUTED)])
        style.configure("TSpinbox", fieldbackground=FIELD, background=FIELD,
                        foreground=TEXT, arrowcolor=TEXT, padding=7)
        style.configure("Vertical.TScrollbar", background=FIELD, troughcolor=PANEL,
                        arrowcolor=MUTED, borderwidth=0)
        self.root.option_add("*TCombobox*Listbox.background", FIELD)
        self.root.option_add("*TCombobox*Listbox.foreground", TEXT)
        self.root.option_add("*TCombobox*Listbox.selectBackground", "#59332d")

    def _build(self):
        shell = ttk.Frame(self.root, padding=(28, 22, 28, 18))
        shell.pack(fill="both", expand=True)
        shell.columnconfigure(0, weight=1)
        shell.rowconfigure(2, weight=1)
        header = ttk.Frame(shell)
        header.grid(row=0, column=0, sticky="ew")
        logo = tk.Label(header, text="u", font=("Ubuntu", 26, "bold"), bg=ORANGE,
                        fg="white", width=2, padx=2, pady=1)
        logo.pack(side="left", padx=(0, 14))
        titles = ttk.Frame(header)
        titles.pack(side="left", fill="x", expand=True)
        ttk.Label(titles, text="Uoo Boot Manager", style="Title.TLabel").pack(anchor="w")
        ttk.Label(titles, text="Choose what starts next. Keep a way back.",
                  style="Muted.TLabel").pack(anchor="w", pady=(3, 0))
        self.mode = tk.Label(header, text="READING", bg=FIELD, fg=MUTED,
                             font=("Ubuntu", 10, "bold"), padx=12, pady=7)
        self.mode.pack(side="right")

        notice = ttk.Frame(shell, style="Card.TFrame", padding=16)
        notice.grid(row=1, column=0, sticky="ew", pady=(22, 18))
        self.notice_title = ttk.Label(notice, text="Reading your boot configuration…",
                                      style="Card.TLabel", font=("Ubuntu", 12, "bold"))
        self.notice_title.pack(anchor="w")
        self.notice_text = ttk.Label(notice, text="The desktop app runs without administrator privileges.",
                                     style="CardMuted.TLabel", wraplength=1020)
        self.notice_text.pack(anchor="w", pady=(5, 0), fill="x")
        notice.bind("<Configure>", lambda event: self.notice_text.configure(wraplength=max(300, event.width - 32)))

        self.notebook = ttk.Notebook(shell)
        self.notebook.grid(row=2, column=0, sticky="nsew")
        self.grub_tab = ttk.Frame(self.notebook, padding=(0, 18, 0, 0))
        self.firmware_tab = ttk.Frame(self.notebook, padding=(0, 18, 0, 0))
        self.backup_tab = ttk.Frame(self.notebook, padding=(0, 18, 0, 0))
        self.notebook.add(self.grub_tab, text="GRUB boot menu", underline=0)
        self.notebook.add(self.firmware_tab, text="UEFI firmware", underline=0)
        self.notebook.add(self.backup_tab, text="Backups", underline=0)
        for tab in (self.grub_tab, self.firmware_tab, self.backup_tab):
            tab.columnconfigure(0, weight=1)
            tab.rowconfigure(2, weight=1)
        self._build_grub()
        self._build_firmware()
        self._build_backups()

        footer = ttk.Frame(shell)
        footer.grid(row=3, column=0, sticky="ew", pady=(18, 0))
        footer.columnconfigure(0, weight=1)
        self.status = tk.StringVar(value="Reading configuration…")
        status_label = ttk.Label(footer, textvariable=self.status, style="Muted.TLabel", wraplength=600)
        status_label.grid(row=0, column=0, sticky="ew")
        status_label.bind("<Configure>", lambda event: status_label.configure(wraplength=max(180, event.width)))
        self.admin_reload = ttk.Button(footer, text="Read as administrator", command=self.read_as_administrator)
        self.admin_reload.grid(row=0, column=1, padx=(12, 0))
        self.reload_button = ttk.Button(footer, text="Reload  ·  Ctrl+R", command=self.reload)
        self.reload_button.grid(row=0, column=2, padx=(12, 0))

    def _table(self, parent, columns, headings, widths):
        frame = ttk.Frame(parent, style="Card.TFrame")
        frame.grid(row=2, column=0, sticky="nsew")
        tree = ttk.Treeview(frame, columns=columns, show="headings", selectmode="browse", height=5)
        for column, heading, width in zip(columns, headings, widths):
            tree.heading(column, text=heading)
            tree.column(column, width=width, minwidth=70, stretch=True, anchor="w")
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        tree.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        return tree

    def _button(self, parent, text, command, accent=False, side="left", **pack):
        button = ttk.Button(parent, text=text, command=command,
                            style="Accent.TButton" if accent else "TButton")
        button.pack(side=side, **pack)
        self.action_buttons.append(button)
        return button

    def _build_grub(self):
        ttk.Label(self.grub_tab, text="Operating systems & recovery", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        self.default_label = ttk.Label(self.grub_tab, text="", style="Muted.TLabel")
        self.default_label.grid(row=1, column=0, sticky="w", pady=(5, 12))
        self.grub_tree = self._table(self.grub_tab, ("title", "group", "default"),
                                     ("BOOT ENTRY", "MENU GROUP", "CURRENT DEFAULT"), (480, 260, 140))
        self.grub_tree.bind("<<TreeviewSelect>>", self._selection_changed)
        self.grub_tree.bind("<Double-1>", lambda _event: self.apply_grub())
        self.grub_tree.bind("<Return>", lambda _event: self.apply_grub())

        controls = ttk.Frame(self.grub_tab, style="Card.TFrame", padding=16)
        controls.grid(row=3, column=0, sticky="ew", pady=(14, 0))
        timing = ttk.Frame(controls, style="Card.TFrame")
        timing.pack(side="left")
        ttk.Label(timing, text="Menu timeout (seconds)", style="CardMuted.TLabel").pack(anchor="w", pady=(0, 7))
        self.timeout = tk.StringVar(value="10")
        spin = ttk.Spinbox(timing, from_=-1, to=300, textvariable=self.timeout, width=10)
        spin.pack(anchor="w")
        self.edit_widgets.append((spin, "normal"))
        presentation = ttk.Frame(controls, style="Card.TFrame")
        presentation.pack(side="left", padx=(24, 0))
        ttk.Label(presentation, text="Menu appearance", style="CardMuted.TLabel").pack(anchor="w", pady=(0, 7))
        self.menu_style = tk.StringVar(value="Show menu")
        combo = ttk.Combobox(presentation, values=list(STYLES), textvariable=self.menu_style,
                             width=16, state="readonly")
        combo.pack(anchor="w")
        self.edit_widgets.append((combo, "readonly"))
        self.grub_apply = self._button(controls, "Apply boot settings", self.apply_grub,
                                      accent=True, side="right", padx=(15, 0), anchor="s")
        self.grub_hint = ttk.Label(self.grub_tab, text="Select an entry to make it the default. −1 waits indefinitely; 0 boots immediately.",
                                   style="Muted.TLabel", wraplength=1020)
        self.grub_hint.grid(row=4, column=0, sticky="ew", pady=(10, 0))
        self.grub_hint.bind("<Configure>", lambda event: self.grub_hint.configure(wraplength=max(200, event.width)))

    def _build_firmware(self):
        ttk.Label(self.firmware_tab, text="Firmware boot order", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        self.firmware_label = ttk.Label(self.firmware_tab, text="", style="Muted.TLabel", wraplength=1000)
        self.firmware_label.grid(row=1, column=0, sticky="ew", pady=(5, 12))
        self.firmware_label.bind("<Configure>", lambda event: self.firmware_label.configure(wraplength=max(200, event.width)))
        self.firmware_tree = self._table(self.firmware_tab, ("position", "label", "id", "status"),
                                         ("ORDER", "FIRMWARE ENTRY", "ID", "STATUS"), (80, 440, 90, 270))
        self.firmware_tree.bind("<<TreeviewSelect>>", self._selection_changed)
        actions = ttk.Frame(self.firmware_tab)
        actions.grid(row=3, column=0, sticky="ew", pady=(14, 0))
        self.move_up = self._button(actions, "Move up", lambda: self.move_firmware(-1), padx=(0, 8))
        self.move_down = self._button(actions, "Move down", lambda: self.move_firmware(1), padx=(0, 8))
        self.order_apply = self._button(actions, "Save boot order", self.apply_order, accent=True, side="right")

        once = ttk.Frame(self.firmware_tab, style="Card.TFrame", padding=(16, 10))
        once.grid(row=4, column=0, sticky="ew", pady=(14, 0))
        heading = ttk.Frame(once, style="Card.TFrame")
        heading.pack(fill="x", pady=(0, 8))
        ttk.Label(heading, text="Next boot only", style="Card.TLabel", font=("Ubuntu", 12, "bold")).pack(side="left")
        ttk.Label(heading, text="Override the saved order for one restart.",
                  style="CardMuted.TLabel").pack(side="right")
        row = ttk.Frame(once, style="Card.TFrame")
        row.pack(fill="x")
        self.next_apply = self._button(row, "Use selected next boot", lambda: self.next_boot(False), padx=(0, 8))
        self.next_clear = self._button(row, "Clear next boot", lambda: self.next_boot(True))
        self.next_label = ttk.Label(row, text="", style="CardMuted.TLabel")
        self.next_label.pack(side="right", padx=(16, 0))

    def _build_backups(self):
        ttk.Label(self.backup_tab, text="Restore a previous configuration", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(self.backup_tab, text="A backup is created before every change. Restoring also saves the current configuration.",
                  style="Muted.TLabel", wraplength=820).grid(row=1, column=0, sticky="ew", pady=(5, 12))
        self.backup_tree = self._table(self.backup_tab, ("created", "kind", "summary"),
                                       ("CREATED", "TYPE", "CHANGE"), (200, 140, 540))
        self.backup_tree.bind("<<TreeviewSelect>>", self._selection_changed)
        actions = ttk.Frame(self.backup_tab)
        actions.grid(row=3, column=0, sticky="ew", pady=(14, 0))
        self.backup_refresh = self._button(actions, "Load backups", self.load_backups)
        self.restore_button = self._button(actions, "Restore selected backup", self.restore,
                                           accent=True, side="right")
        self.backup_hint = ttk.Label(self.backup_tab,
                                     text="Load backups to see saved changes. Administrator authentication is required on your system.",
                                     style="Muted.TLabel", wraplength=1000)
        self.backup_hint.grid(row=4, column=0, sticky="ew", pady=(12, 0))
        self.backup_hint.bind("<Configure>", lambda event: self.backup_hint.configure(wraplength=max(200, event.width)))

    def _poll(self):
        if self.closed:
            return
        while True:
            try:
                callback, result, error = self.events.get_nowait()
            except queue.Empty:
                break
            self.busy = False
            self._update_controls()
            if error:
                self.status.set("The action could not be completed.")
                messagebox.showerror("Uoo Boot Manager", str(error), parent=self.root)
            else:
                callback(result)
        self.root.after(80, self._poll)

    def _job(self, label, work, callback):
        if self.busy or self.closed:
            return
        self.busy = True
        self.status.set(label)
        self._update_controls()

        def worker():
            try:
                self.events.put((callback, work(), None))
            except Exception as exc:
                self.events.put((callback, None, exc))

        threading.Thread(target=worker, daemon=True).start()

    def reload(self):
        if self.busy:
            return
        if self.demo and self.snapshot:
            self._render()
            self.status.set("Preview refreshed. Changes stay in this session.")
            return

        def read():
            if self.explicit_demo:
                return system.demo_snapshot()
            snapshot = system.snapshot()
            if (self.auto_demo and not snapshot.get("entries") and not snapshot.get("firmware", {}).get("entries")
                    and not snapshot.get("settings") and not Path("/boot/grub").exists()
                    and not Path("/sys/firmware/efi").exists()):
                preview = system.demo_snapshot()
                preview["preview_reason"] = "No readable GRUB or UEFI boot entries were found."
                return preview
            return snapshot

        self._job("Reading boot configuration…", read, self._loaded)

    def read_as_administrator(self):
        if not self.busy and self.installed and not self.demo:
            self._job("Authenticating to read the boot configuration…",
                      lambda: run_helper({"action": "snapshot"}), lambda response: self._loaded(response["snapshot"]))

    def _loaded(self, snapshot):
        self.snapshot = snapshot
        self.demo = bool(snapshot.get("demo"))
        self._render()
        self.status.set("Preview ready. System boot settings are untouched." if self.demo else "Configuration loaded.")

    def _render(self):
        self.installed = trusted_helper_available()
        settings = self.snapshot.get("settings", {})
        entries = self.snapshot.get("entries", [])
        self.grub_tree.delete(*self.grub_tree.get_children())
        default = str(settings.get("GRUB_DEFAULT", "0"))
        resolved_default = settings.get("UOO_SAVED_ENTRY", default) if default == "saved" else default
        chosen = None
        for index, entry in enumerate(entries):
            current = self._matches_default(entry, resolved_default)
            iid = f"grub-{index}"
            self.grub_tree.insert("", "end", iid=iid,
                                  values=(entry["title"], entry.get("group") or "Main menu", "Default" if current else ""))
            if current:
                chosen = iid
        if chosen:
            self.grub_tree.selection_set(chosen)
        self.timeout.set(str(settings.get("GRUB_TIMEOUT", "10")))
        style = settings.get("GRUB_TIMEOUT_STYLE", "menu")
        self.menu_style.set(next((label for label, value in STYLES.items() if value == style), "Show menu"))
        default_entry = next((entry["title"] for entry in entries
                              if self._matches_default(entry, resolved_default)), resolved_default)
        if default == "saved":
            default_entry += " (saved entry)"
        self.default_label.configure(text=f"Current default: {default_entry}" if entries else "No readable GRUB menu was found.")
        hint = "Select an entry to make it the default. −1 waits indefinitely; 0 boots immediately."
        if entries and not chosen:
            hint = "The current default could not be resolved. Select the desired boot entry before applying settings."
        self.grub_hint.configure(text=hint)

        firmware = self.snapshot.get("firmware", {})
        self.order = list(firmware.get("order", []))
        self._render_firmware()
        self._render_backups()
        if self.demo:
            self.mode.configure(text="PREVIEW", fg="#ffc09b", bg="#523227")
            self.notice_title.configure(text="Preview mode — explore safely")
            reason = self.snapshot.get("preview_reason", "")
            self.notice_text.configure(text=(reason + " " if reason else "")
                                       + "Changes stay in this app and reset when you close it. No system files or firmware are modified.")
            self.backup_hint.configure(text="Preview backups are kept only for this session. Apply a change to create the first one.")
        else:
            self.mode.configure(text="LIVE" if self.installed else "READ ONLY", fg="#b9e8c6" if self.installed else MUTED,
                                bg="#253d31" if self.installed else FIELD)
            errors = list(self.snapshot.get("errors", []))
            if self.installed:
                self.notice_title.configure(text="Your boot configuration")
                text = "Changes require administrator authentication. Uoo creates a backup before applying them. Use Read as administrator if entries are inaccessible."
            else:
                self.notice_title.configure(text="Read-only session")
                text = "Install the Uoo .deb package to enable changes and backups, then open Uoo from the applications menu."
            if errors:
                text += " " + " ".join(errors)
            self.notice_text.configure(text=text)
        self._update_controls()

    @staticmethod
    def _matches_default(entry, default):
        title_path = (entry.get("group", "").replace(" / ", ">") + ">" if entry.get("group") else "") + entry["title"]
        return default in (entry["selector"], entry["position"], title_path)

    def _render_firmware(self, selection=None):
        firmware = self.snapshot.get("firmware", {})
        entries = {entry["id"]: entry for entry in firmware.get("entries", [])}
        self.firmware_tree.delete(*self.firmware_tree.get_children())
        ordered = self.order + [identifier for identifier in entries if identifier not in self.order]
        for identifier in ordered:
            entry = entries.get(identifier, {"label": "Unavailable boot entry", "active": False})
            flags = []
            if identifier == firmware.get("current"):
                flags.append("Current boot")
            if identifier == firmware.get("next"):
                flags.append("Next boot")
            if not entry.get("active", True):
                flags.append("Inactive")
            position = str(self.order.index(identifier) + 1) if identifier in self.order else "—"
            if identifier not in self.order:
                flags.append("Outside boot order")
            self.firmware_tree.insert("", "end", iid=identifier,
                                      values=(position, entry["label"], identifier, " · ".join(flags)))
        if selection in ordered:
            self.firmware_tree.selection_set(selection)
            self.firmware_tree.see(selection)
        elif ordered:
            self.firmware_tree.selection_set(ordered[0])
        if firmware.get("available"):
            text = "Move an entry up or down, then save. Entries outside the order can be used for the next boot."
            if any(identifier not in entries for identifier in self.order):
                text = "The saved order contains an unavailable entry. Order changes are disabled until the firmware entries are consistent."
            self.firmware_label.configure(text=text)
        else:
            self.firmware_label.configure(text=firmware.get("error") or "UEFI firmware is unavailable. Legacy BIOS systems use the GRUB tab.")
        next_id = firmware.get("next", "")
        label = entries.get(next_id, {}).get("label", next_id)
        self.next_label.configure(text=f"Scheduled: {label}" if next_id else "No next-boot override")

    def _render_backups(self):
        previous = self.backup_tree.selection()
        self.backup_tree.delete(*self.backup_tree.get_children())
        for backup in self.backups:
            created = backup.get("created", "")
            try:
                created = datetime.fromisoformat(created).astimezone().strftime("%Y-%m-%d %H:%M:%S")
            except ValueError:
                pass
            kind = {"grub": "GRUB", "uefi": "UEFI order", "next_boot": "Next boot"}.get(backup.get("kind"), backup.get("kind", ""))
            self.backup_tree.insert("", "end", iid=backup["id"],
                                    values=(created, kind, backup.get("summary", "")))
        if previous and self.backup_tree.exists(previous[0]):
            self.backup_tree.selection_set(previous[0])
        elif self.backups:
            self.backup_tree.selection_set(self.backups[0]["id"])

    def _selection_changed(self, _event=None):
        self._update_controls()

    def _update_controls(self):
        enabled = not self.busy and (self.demo or self.installed)
        for button in self.action_buttons:
            button.configure(state="normal" if enabled else "disabled")
        for widget, normal_state in self.edit_widgets:
            widget.configure(state=normal_state if not self.busy else "disabled")
        self.reload_button.configure(state="disabled" if self.busy else "normal")
        self.admin_reload.configure(state="normal" if not self.busy and self.installed and not self.demo else "disabled")
        self.grub_apply.configure(state="normal" if enabled and self.grub_tree.selection() else "disabled")
        firmware = self.snapshot.get("firmware", {})
        selected = self.firmware_tree.selection()
        identifier = selected[0] if selected else None
        index = self.order.index(identifier) if identifier in self.order else -1
        live_firmware = enabled and bool(firmware.get("available"))
        known = {entry["id"] for entry in firmware.get("entries", [])}
        self.move_up.configure(state="normal" if live_firmware and index > 0 else "disabled")
        self.move_down.configure(state="normal" if live_firmware and 0 <= index < len(self.order) - 1 else "disabled")
        self.order_apply.configure(state="normal" if live_firmware and self.order
                                   and set(self.order) <= known and self.order != firmware.get("order", []) else "disabled")
        self.next_apply.configure(state="normal" if live_firmware and identifier in known else "disabled")
        self.next_clear.configure(state="normal" if live_firmware and firmware.get("next") else "disabled")
        self.restore_button.configure(state="normal" if enabled and self.backup_tree.selection() else "disabled")

    def _can_change(self):
        return not self.busy and (self.demo or self.installed)

    def _confirm(self, title, description):
        ending = "This is a preview. Your computer will not be modified." if self.demo else "Uoo will create a backup and ask for administrator authentication."
        return messagebox.askokcancel(title, description + "\n\n" + ending,
                                      parent=self.root, default="cancel")

    def apply_grub(self):
        if not self._can_change() or not self.grub_tree.selection():
            return
        index = int(self.grub_tree.selection()[0].split("-")[1])
        entry = self.snapshot["entries"][index]
        try:
            timeout = int(self.timeout.get())
            if not -1 <= timeout <= 300:
                raise ValueError
        except ValueError:
            messagebox.showerror("Check the timeout", "Enter a whole number from −1 to 300.\n−1 waits indefinitely; 0 boots immediately.", parent=self.root)
            return
        style = STYLES.get(self.menu_style.get(), "menu")
        timeout_label = "Wait indefinitely" if timeout == -1 else f"{timeout} seconds"
        summary = f"Default: {entry['title']}\nTimeout: {timeout_label}\nAppearance: {self.menu_style.get()}"
        if self._confirm("Apply GRUB settings", summary):
            self._change({"action": "apply_grub", "entry": entry["selector"], "timeout": timeout, "style": style},
                         f"Default boot: {entry['title']}")

    def move_firmware(self, direction):
        if not self._can_change() or not self.firmware_tree.selection():
            return
        identifier = self.firmware_tree.selection()[0]
        if identifier not in self.order:
            return
        index = self.order.index(identifier)
        target = index + direction
        if 0 <= target < len(self.order):
            self.order[index], self.order[target] = self.order[target], self.order[index]
            self._render_firmware(identifier)
            self._update_controls()
            self.status.set("Boot order edited. Save boot order to apply it.")

    def apply_order(self):
        if not self._can_change() or not self.order:
            return
        entries = {entry["id"]: entry for entry in self.snapshot["firmware"]["entries"]}
        summary = "New firmware boot order:\n\n" + "\n".join(
            f"{index + 1}. {entries[identifier]['label']} ({identifier})" for index, identifier in enumerate(self.order))
        if self._confirm("Save firmware boot order", summary):
            self._change({"action": "apply_order", "order": list(self.order)}, "Firmware boot order changed")

    def next_boot(self, clear):
        if not self._can_change():
            return
        selected = self.firmware_tree.selection()
        if not clear and not selected:
            return
        identifier = "" if clear else selected[0]
        entries = {entry["id"]: entry for entry in self.snapshot["firmware"]["entries"]}
        summary = "Clear the next-boot override. The saved firmware order will be used." if clear else (
            f"Use {entries[identifier]['label']} ({identifier}) for the next boot only.\nThe saved boot order stays in place.")
        if self._confirm("Next boot only", summary):
            self._change({"action": "next_boot", "entry": identifier}, "Next boot cleared" if clear else f"Next boot: {entries[identifier]['label']}")

    def _save_demo_backup(self, kind, summary):
        self.demo_counter += 1
        identifier = f"preview-{self.demo_counter:04d}"
        self.demo_states[identifier] = copy.deepcopy(self.snapshot)
        self.backups.insert(0, {"id": identifier, "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                "kind": kind, "summary": summary})

    def _change(self, request, summary):
        if self.demo:
            action = request["action"]
            kind = {"apply_grub": "grub", "apply_order": "uefi", "next_boot": "next_boot"}.get(action)
            restored = None
            if action == "restore":
                restored = copy.deepcopy(self.demo_states[request["backup"]])
                kind = next(backup["kind"] for backup in self.backups if backup["id"] == request["backup"])
            self._save_demo_backup(kind, "Configuration before: " + summary)
            if action == "apply_grub":
                self.snapshot["settings"].update(GRUB_DEFAULT=request["entry"], GRUB_TIMEOUT=str(request["timeout"]),
                                                 GRUB_TIMEOUT_STYLE=request["style"])
                self.snapshot["managed"] = True
            elif action == "apply_order":
                self.snapshot["firmware"]["order"] = list(request["order"])
            elif action == "next_boot":
                self.snapshot["firmware"]["next"] = request["entry"]
            elif action == "restore":
                if kind == "grub":
                    self.snapshot["settings"] = restored["settings"]
                    self.snapshot["managed"] = restored["managed"]
                elif kind == "uefi":
                    self.snapshot["firmware"]["order"] = restored["firmware"]["order"]
                elif kind == "next_boot":
                    self.snapshot["firmware"]["next"] = restored["firmware"]["next"]
            self._render()
            self.status.set("Preview updated. " + summary + ".")
            return

        def complete(response):
            self.status.set(response.get("message", "Change applied successfully."))
            if "backups" in response:
                self.backups = response["backups"]
            if "snapshot" in response:
                self._loaded(response["snapshot"])
                self.status.set(response.get("message", "Change applied successfully."))
            else:
                self.reload()

        self._job("Waiting for administrator authentication and applying changes…",
                  lambda: run_helper(request), complete)

    def load_backups(self):
        if self.busy:
            return
        if self.demo:
            self._render_backups()
            self._update_controls()
            self.status.set(f"{len(self.backups)} preview backup(s) in this session.")
            return
        if not self.installed:
            return

        def complete(response):
            self.backups = response.get("backups", [])
            self._render_backups()
            self._update_controls()
            self.backup_hint.configure(text="Select a backup to restore its saved configuration." if self.backups else "No backups yet. Apply a boot setting to create the first one.")
            self.status.set(f"Loaded {len(self.backups)} backup(s).")

        self._job("Authenticating to read saved backups…", lambda: run_helper({"action": "list_backups"}), complete)

    def restore(self):
        if not self._can_change() or not self.backup_tree.selection():
            return
        identifier = self.backup_tree.selection()[0]
        backup = next(item for item in self.backups if item["id"] == identifier)
        summary = f"Restore the configuration saved at {backup.get('created', '')}.\n\n{backup.get('summary', '')}\n\nThe current configuration will be backed up first."
        if self._confirm("Restore boot configuration", summary):
            self._change({"action": "restore", "backup": identifier}, "Restored saved configuration")

    def close(self):
        if self.busy and not messagebox.askokcancel(
                "Action in progress", "An administrator action may still be running. Closing this window does not cancel it.\n\nClose Uoo?",
                parent=self.root, default="cancel"):
            return
        self.closed = True
        self.root.destroy()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Uoo Boot Manager for Ubuntu")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--demo", action="store_true", help="open a safe preview with sample boot entries")
    mode.add_argument("--live", action="store_true", help="show this computer's configuration without automatic preview fallback")
    args = parser.parse_args(argv)
    try:
        root = tk.Tk(className="UooBootManager")
    except tk.TclError as exc:
        parser.exit(1, f"Uoo needs a graphical desktop session: {exc}\n")
    App(root, demo=args.demo, auto_demo=not args.live)
    root.mainloop()
    return 0
