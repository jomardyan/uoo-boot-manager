# The payload comes from the same reviewed staging tree as the Debian package.
%global _build_id_links none
%global __os_install_post %{nil}

Name: uoo-boot-manager
Version: %{uoo_version}
%if 0%{?uoo_opensuse}
Release: 1.opensuse
%else
Release: 1
%endif
Summary: Native desktop manager for GRUB and UEFI boot settings
License: LicenseRef-Not-Specified
BuildArch: noarch
Source0: payload.tar.gz
AutoReqProv: no
Requires: python3 >= 3.10
Requires: python3-gobject
%if 0%{?uoo_opensuse}
Requires: python3-gobject-Gdk
Requires: typelib-1_0-Gtk-4_0 >= 4.6
Requires: typelib-1_0-Adw-1 >= 1.5
Requires: girepository-1_0
%else
# Fedora packages cairo-1.0.typelib separately from the Python GI bindings.
Requires: gobject-introspection
Requires: gtk4 >= 4.6
Requires: libadwaita >= 1.5
%endif
Requires: polkit

%description
Manage boot menus, firmware entries, backups, and boot configuration with a
native GTK4 desktop and an authenticated system helper. GRUB modification and
bootloader repair currently require the Ubuntu tools and layout; installing
this RPM does not establish Fedora GRUB or repair support.

%prep
%setup -q -c -T
tar -xzf %{SOURCE0}

%build

%install
mkdir -p %{buildroot}
cp -a usr %{buildroot}/

%files
%defattr(-,root,root,-)
/usr/bin/uoo-boot-manager
/usr/bin/uoo-boot-helper
/usr/bin/uoo-boot-inspect
%dir /usr/lib/uoo-boot-manager
/usr/lib/uoo-boot-manager/launch.py
/usr/lib/uoo-boot-manager/helper.py
/usr/lib/uoo-boot-manager/inspector.py
/usr/lib/uoo-boot-manager/uoo
/usr/share/applications/uoo-boot-manager.desktop
/usr/share/icons/hicolor/scalable/apps/uoo-boot-manager.svg
/usr/share/polkit-1/actions/org.uoo.boot-manager.policy
/usr/share/doc/uoo-boot-manager
