# Security Policy

## Supported Versions
Only the latest version of `tidal-sync` receives security updates.

## Reporting a Vulnerability
If you discover a security flaw or a potential credential leak in the logic, please do not open a public issue.
Instead, email: **[infoLeonid@protonmail.com](mailto:infoLeonid@protonmail.com)**.

### A Note on Tokens
This tool stores OAuth tokens locally in `~/.tidal_sync/`. Never share your JSON files or push them to GitHub. The included `.gitignore` is designed to prevent this, but the ultimate responsibility lies with you, the end-user.