# Run `make help` for targets and docs/PACKAGING.md for native prerequisites.
.DEFAULT_GOAL := help
.DELETE_ON_ERROR:
.ONESHELL:

SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c

PYTHON ?= python3
DOTNET ?= dotnet
POWERSHELL ?= pwsh
NODE ?= node
WINGET_VERSION ?= $(shell "$(PYTHON)" scripts/package-version.py)
WINDOWS_RUNTIME ?= win-x64
WINDOWS_BUILD_ARGS ?=
# Select formats appropriate to your build host, e.g. PACKAGE_FORMATS="deb rpm".
PACKAGE_FORMATS ?= deb rpm rpm-opensuse arch appimage flatpak snap
# Extra disposable-VM options, e.g. VM_ARGS="--boot-mode bios".
VM_ARGS ?=
# auto: terminal only; always: include ANSI colors in logs; never: plain text.
COLOR ?= auto
# Install dependencies for all host-supported operations, or choose Make targets.
DEPS_FOR ?= everything
DEPS_ARGS ?=
DEPENDENCY_TARGETS := help all build deb rpm rpm-opensuse arch appimage flatpak snap \
	packages version test test-gtk smoke demo check check-package checksums \
	verify-checksums windows windows-test vm-test icons website winget clean distclean
# These defaults match deps-appimage; environment/command-line overrides win.
BUILD_PYTHON ?= $(CURDIR)/build/appimage-venv/bin/python
APPIMAGETOOL ?= $(CURDIR)/build/tools/appimagetool.AppImage
APPIMAGETOOL_RUNTIME_FILE ?= $(CURDIR)/build/tools/runtime
export BUILD_PYTHON APPIMAGETOOL APPIMAGETOOL_RUNTIME_FILE

define output_colors
@uoo_reset=''; uoo_bold=''; uoo_cyan=''; uoo_green=''; uoo_red=''; uoo_yellow=''
case "$(COLOR)" in
  auto|always|never) ;;
  *) printf 'Invalid COLOR: %s (use auto, always, or never)\n' "$(COLOR)" >&2; exit 2 ;;
esac
if [[ -z "$${NO_COLOR:-}" && ( "$(COLOR)" == always || ( "$(COLOR)" == auto && -t 1 && "$${TERM:-}" != dumb ) ) ]]; then
  uoo_reset='\033[0m'; uoo_bold='\033[1m'; uoo_cyan='\033[36m'
  uoo_green='\033[32m'; uoo_red='\033[31m'; uoo_yellow='\033[33m'
fi
endef

# One shell per recipe keeps the exit trap active across every command.
# Preserve failures and report success only after the entire target completes.
define task_status
$(output_colors)
printf '%b==> %s%b\n' "$${uoo_bold}$${uoo_cyan}" '$@' "$${uoo_reset}"
trap 'uoo_status=$$?; if (( uoo_status == 0 )); then printf "%b[OK] %s%b\n" "$${uoo_green}" "$@" "$${uoo_reset}"; else printf "%b[FAIL] %s (exit %s)%b\n" "$${uoo_red}" "$@" "$$uoo_status" "$${uoo_reset}" >&2; fi; exit "$$uoo_status"' EXIT
endef

.PHONY: help all build deb rpm rpm-opensuse arch appimage flatpak snap packages \
	version test test-gtk smoke demo check check-package checksums verify-checksums \
	windows windows-test vm-test icons website winget clean distclean
.PHONY: deps install-deps deps-all $(addprefix deps-,$(DEPENDENCY_TARGETS))

help:
	$(output_colors)
	printf '%b\n' \
	  "$${uoo_bold}$${uoo_cyan}Uoo Boot Manager — build and check commands (GNU Make + Bash)$${uoo_reset}" \
	  '' \
	  "$${uoo_cyan}  make deps$${uoo_reset}         Install dependencies for all host-supported operations" \
	  "$${uoo_cyan}  make deps-<target>$${uoo_reset} Install dependencies for one operation (e.g. deps-windows)" \
	  "$${uoo_cyan}  make deps DEPS_ARGS=--dry-run$${uoo_reset}  Preview installs without changes" \
	  '' \
	  "$${uoo_cyan}  make [help]$${uoo_reset}        Show this help (default; builds nothing)" \
	  "$${uoo_cyan}  make build$${uoo_reset}        Build the Debian package (also: all, deb)" \
	  "$${uoo_cyan}  make check$${uoo_reset}        Run tests, build Debian, and validate its archive" \
	  "$${uoo_cyan}  make test$${uoo_reset}         Run Python tests; GTK tests skip without dependencies/display" \
	  "$${uoo_cyan}  make test-gtk$${uoo_reset}     Run GTK tests under Xvfb; requires GTK4/libadwaita 1.5+" \
	  "$${uoo_cyan}  make smoke$${uoo_reset}        Check source desktop help and demo startup under Xvfb" \
	  "$${uoo_cyan}  make demo$${uoo_reset}         Start the interactive source demo" \
	  "$${uoo_cyan}  make icons$${uoo_reset}        Regenerate platform icons and favicons from SVG masters" \
	  "$${uoo_cyan}  make website$${uoo_reset}      Build and check the static landing page for your hosting" \
	  "$${uoo_cyan}  make winget$${uoo_reset}       Prepare manifests from verified published Windows installers" \
	  "$${uoo_cyan}  make version$${uoo_reset}      Validate and print the Linux release version" \
	  "$${uoo_cyan}  make check-package$${uoo_reset} Build and validate the Debian archive" \
	  "$${uoo_cyan}  make rpm$${uoo_reset}          Build and validate the Fedora RPM from Debian" \
	  "$${uoo_cyan}  make rpm-opensuse$${uoo_reset} Build and validate the openSUSE RPM from Debian" \
	  "$${uoo_cyan}  make arch$${uoo_reset}         Build and validate Arch package (ordinary user on Arch)" \
	  "$${uoo_cyan}  make appimage$${uoo_reset}     Build native AppImage (requires configured bundler/tools)" \
	  "$${uoo_cyan}  make flatpak$${uoo_reset}      Build native Flatpak (requires GNOME platform and SDK)" \
	  "$${uoo_cyan}  make snap$${uoo_reset}         Build AppImage then Snap (Snapcraft destructive mode)" \
	  "$${uoo_cyan}  make packages$${uoo_reset}     Build PACKAGE_FORMATS, then generate Linux checksums" \
	  "$${uoo_cyan}  make checksums$${uoo_reset}    Generate dist/SHA256SUMS for existing Linux packages" \
	  "$${uoo_cyan}  make verify-checksums$${uoo_reset}  Verify the existing Linux checksum manifest" \
	  "$${uoo_cyan}  make windows$${uoo_reset}      Build Windows ZIP; also build installer on Windows" \
	  "$${uoo_cyan}  make windows-test$${uoo_reset} Run portable .NET model/protocol tests" \
	  "$${uoo_cyan}  make vm-test$${uoo_reset}      Build Debian and exercise it in a disposable Ubuntu VM" \
	  "$${uoo_cyan}  make clean$${uoo_reset}        Delete build/ staging (including cached build tools)" \
	  "$${uoo_cyan}  make distclean$${uoo_reset}    Delete build/ and dist/ outputs" \
	  '' \
	  "$${uoo_yellow}Overrides:$${uoo_reset} PYTHON, DOTNET, POWERSHELL, WINDOWS_RUNTIME, WINDOWS_BUILD_ARGS," \
	  '           PACKAGE_FORMATS, VM_ARGS; AppImage reads BUILD_PYTHON,' \
	  '           APPIMAGETOOL and APPIMAGETOOL_RUNTIME_FILE from the environment.' \
	  '           DEPS_FOR="windows test-gtk" selects operations; DEPS_ARGS=--dry-run previews.' \
	  "$${uoo_yellow}Colors:$${uoo_reset}    COLOR=auto (default), always, or never; NO_COLOR disables colors." \
	  'See docs/PACKAGING.md and docs/WINDOWS.md for platform prerequisites.'

deps install-deps deps-all:
	$(task_status)
	DOTNET="$(DOTNET)" POWERSHELL="$(POWERSHELL)" "$(PYTHON)" scripts/install-deps.py --package-formats "$(PACKAGE_FORMATS)" $(DEPS_ARGS) $(DEPS_FOR)

$(addprefix deps-,$(filter-out all,$(DEPENDENCY_TARGETS))):
	$(task_status)
	DOTNET="$(DOTNET)" POWERSHELL="$(POWERSHELL)" "$(PYTHON)" scripts/install-deps.py --package-formats "$(PACKAGE_FORMATS)" $(DEPS_ARGS) $(@:deps-%=%)

all build: deb

version:
	$(task_status)
	"$(PYTHON)" scripts/package-version.py

icons:
	$(task_status)
	"$(PYTHON)" scripts/build-icons.py

website:
	$(task_status)
	"$(NODE)" website/build.mjs
	"$(NODE)" --check website/app.js
	"$(PYTHON)" scripts/check-website.py

winget:
	$(task_status)
	"$(PYTHON)" scripts/build-winget.py --version "$(WINGET_VERSION)"

deb:
	$(task_status)
	bash scripts/build-deb.sh

check-package: deb
	$(task_status)
	@version="$$('$(PYTHON)' scripts/package-version.py)"; \
	  "$(PYTHON)" scripts/check-package.py "dist/uoo-boot-manager_$${version}_all.deb"

rpm: check-package
	$(task_status)
	@version="$$('$(PYTHON)' scripts/package-version.py)"; \
	  bash scripts/build-rpm.sh "dist/uoo-boot-manager_$${version}_all.deb"

rpm-opensuse: check-package
	$(task_status)
	@version="$$('$(PYTHON)' scripts/package-version.py)"; \
	  bash scripts/build-rpm.sh --opensuse "dist/uoo-boot-manager_$${version}_all.deb"

arch: check-package
	$(task_status)
	@version="$$('$(PYTHON)' scripts/package-version.py)"; \
	  bash scripts/build-arch.sh "dist/uoo-boot-manager_$${version}_all.deb"

appimage:
	$(task_status)
	bash scripts/build-appimage.sh

flatpak:
	$(task_status)
	bash scripts/build-flatpak.sh

snap: appimage
	$(task_status)
	bash scripts/build-snap.sh

packages: $(PACKAGE_FORMATS)
	$(task_status)
	"$(PYTHON)" scripts/release-checksums.py dist

checksums:
	$(task_status)
	"$(PYTHON)" scripts/release-checksums.py dist

verify-checksums:
	$(task_status)
	cd dist && sha256sum --check SHA256SUMS

test:
	$(task_status)
	"$(PYTHON)" -m unittest discover -s tests -v

test-gtk:
	$(task_status)
	@"$(PYTHON)" -c 'import gi; gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1"); from gi.repository import Adw; assert (Adw.get_major_version(), Adw.get_minor_version()) >= (1, 5), "libadwaita 1.5+ is required"'
	GDK_BACKEND=x11 GSK_RENDERER=cairo dbus-run-session -- xvfb-run -a "$(PYTHON)" -m unittest discover -s tests -p test_gtk_ui.py -v

smoke:
	$(task_status)
	GDK_BACKEND=x11 GSK_RENDERER=cairo dbus-run-session -- xvfb-run -a "$(PYTHON)" scripts/smoke-desktop.py -- "$(PYTHON)" -m uoo

demo:
	$(task_status)
	"$(PYTHON)" -m uoo --demo

# Package tests compare an existing archive against the current source, so
# refresh and validate it before starting tests (also when using make -j).
check: check-package
	$(task_status)
	$(MAKE) --no-print-directory test

windows:
	$(task_status)
	"$(POWERSHELL)" -NoProfile -File windows/scripts/build.ps1 -Runtime "$(WINDOWS_RUNTIME)" $(WINDOWS_BUILD_ARGS)

windows-test:
	$(task_status)
	"$(DOTNET)" run --project windows/Uoo.Tests -c Release

vm-test: check-package
	$(task_status)
	@version="$$('$(PYTHON)' scripts/package-version.py)"; \
	  "$(PYTHON)" scripts/vm-integration.py --package "dist/uoo-boot-manager_$${version}_all.deb" --exercise $(VM_ARGS)

clean:
	$(task_status)
	$(RM) -r -- build

distclean: clean
	$(task_status)
	$(RM) -r -- dist
