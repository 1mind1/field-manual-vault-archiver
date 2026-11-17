Field Manual Vault Archiver — V5.0.2 release candidate (RC3)

Windows: Double-click START_WINDOWS.bat.
Linux: Run bash START_LINUX.sh from a terminal.
macOS: Run bash START_MACOS.command from Terminal, or chmod +x and double-click.

Choose your existing Field Manual Vault root. Option 3 selectively downloads
PDFs/EPUBs. Option 7 refreshes category metadata without document downloads.
Options 1 and 6 work completely offline. Online menus offer discovery pacing.
Options 4/5 require explicit confirmation and can be very large.

Requirements: Python 3.10+ (standard library only; no pip packages).
See README.md for full platform instructions, privacy, limits and testing.

Linux's launcher has passed local offline tests. macOS still needs a real-device
smoke test before support is labeled stable.
