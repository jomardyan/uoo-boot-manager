# Read-only diagnostics for installer dialogs on disposable hosted runners.
param([int]$ProcessId)
$ErrorActionPreference = 'Stop'
if (-not ('Uoo.CiWindowDiagnostics' -as [type])) {
    Add-Type @'
using System;
using System.Runtime.InteropServices;
using System.Text;
using System.Collections.Generic;
namespace Uoo {
    public static class CiWindowDiagnostics {
        public delegate bool Callback(IntPtr window, IntPtr state);
        [DllImport("user32.dll")] static extern bool EnumWindows(Callback callback, IntPtr state);
        [DllImport("user32.dll")] static extern bool EnumChildWindows(IntPtr parent, Callback callback, IntPtr state);
        [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr window, out uint processId);
        [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern int GetWindowText(IntPtr window, StringBuilder text, int count);
        public static List<IntPtr> Owned(uint processId) {
            var result = new List<IntPtr>();
            EnumWindows((window, state) => { uint owner; GetWindowThreadProcessId(window, out owner); if (owner == processId) result.Add(window); return true; }, IntPtr.Zero);
            return result;
        }
        static void Print(IntPtr window) {
            var text = new StringBuilder(2048);
            GetWindowText(window, text, text.Capacity);
            if (text.Length > 0) Console.WriteLine("Installer window: " + text);
        }
        public static void PrintOwned(uint processId) {
            EnumWindows((window, state) => {
                uint owner;
                GetWindowThreadProcessId(window, out owner);
                if (owner == processId) {
                    Print(window);
                    EnumChildWindows(window, (child, unused) => { Print(child); return true; }, IntPtr.Zero);
                }
                return true;
            }, IntPtr.Zero);
        }
    }
}
'@
}
[Uoo.CiWindowDiagnostics]::PrintOwned($ProcessId)
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
foreach ($handle in [Uoo.CiWindowDiagnostics]::Owned($ProcessId)) {
    $window = [System.Windows.Automation.AutomationElement]::FromHandle($handle)
    if (!$window) { continue }
    $controls = $window.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
    foreach ($control in $controls) {
        $name = $control.Current.Name
        if ($name) { Write-Host "Installer accessible control: $name" }
    }
}
