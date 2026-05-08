# 🌊 Tidal Sync CLI

A powerful, high-performance command-line tool to backup, restore, and clone your entire Tidal library. 

Whether you want to safely back up your playlists to a hard drive or perfectly clone your entire library (Liked Songs, Followed Artists, Playlists) to a brand new Tidal account, `tidal-sync` handles it automatically.

## ✨ Key Features
* Exports Playlists, Liked Songs, Saved Albums, and Followed Artists.
* Prevents duplicate tracks from being added. If you already own it, the tool skips it.
* Point the tool at your backup folder, and it will recursively rebuild your entire library automatically.
* Log into multiple accounts at once to easily clone data from a "Source" account to a "Destination" account.
* Uses exact `Tidal IDs` and `ISRC` codes for perfect 1-to-1 matching, falling back to text search only when necessary.
* Logouts utilise IEEE 2883-style logical clearing to securely wipe your credentials from the disk.

---

## ⚖️ Licence & Open Source

This project is licensed under the **GNU Affero General Public Licence v3.0 (AGPLv3)**. 

This licence ensures that the software remains free for the public. Crucially, the **Affero** clause dictates that any person or entity using this code to provide a service over a network (such as a hypothetical web-based "Tidal Migration" service) **must** make their full source code available to the community.

### 🛑 Additional Terms & AI Restriction
**Closed-source commercialisation** of this work is strictly prohibited. Furthermore, I explicitly **withhold consent** for any content in this repository — including, but not limited to code, documentation, and logic — to be used as training data for artificial intelligence (AI) models, large language models (LLMs), or any generative systems. Automated "remixing", adaptation, scraping, or building upon this work by AI entities without my explicit written permission, is strictly prohibited. `:)`

## ⚠️ Disclaimer & Liability

**`tidal-sync` is an independent, open-source educational tool and is NOT affiliated with, endorsed by, or in any way associated with TIDAL Music AS.**

By using this tool, you agree to the following:
1. **Your Responsibilities:** You are solely responsible for how you use this software. This tool interacts with Tidal's API using standard user credentials. It is your responsibility to ensure your usage complies with [Tidal's Terms and Conditions of Use](https://tidal.com/terms).
2. **Account Risks:** Automated interactions with API endpoints can sometimes be flagged by anti-bot or abuse-prevention systems. The author(s) of `tidal-sync` hold **zero liability** for any account warnings, suspensions, bans, or data loss that may occur as a result of using this tool.
3. **No Warranty:** This software is provided "as is", without warranty of any kind. The author(s) shall not be liable for any claims, damages, or legal repercussions arising from the use of this software. 

**Use at your own risk.**

---

## 🚀 Installation

I recommend using [uv](https://docs.astral.sh/uv/) for lightning-fast installation.

**1. Clone the repository:**
```bash
git clone https://github.com/leonid-dalin/tidal-sync
cd tidal-sync
```
**2. Install the tool:**
```bash 
uv pip install -e .
source .venv/bin/activate
```

> Note: _Prefixing the following commands with `uv run` guarantees the tool runs safely inside its own environment. You can avoid that by using `source .venv/bin/activate` (Linux) or `.venv/bin/activate` (Windows)_

---

## 📖 Command Guide

### 1. Login (`login`)

Authenticate via your web browser.
```bash
uv run tidal-sync login
```

* **🔋 Power User Tip:** Use `--profile` (or `-p`) to log into a specific profile. This allows you to manage multiple accounts without logging in and out.
```bash
uv run tidal-sync login -p main_account
uv run tidal-sync login -p backup_account
```

### 2. Export Your Library (`export`)

Downloads your entire library into an organized `Playlists/` and `Favorites/` folder structure.
```bash
uv run tidal-sync export --out ./my_tidal_backup
```

### 3. Import Data (`import`)

You can import a single CSV playlist, or point the tool at an entire directory to rebuild a full library. **Imports are safe:** they automatically skip songs/albums you already have in your library.
```bash
# Import a single playlist
uv run tidal-sync import "My Awesome Playlist.csv"

# Bulk import an entire backup directory
uv run tidal-sync import ./my_tidal_backup
```
* **🔋 Power User Tip:** Route the import to a specific profile using `-p profile_name`.

### 4. Clear Library (`clear`)

⚠ **Destructive Action:** Wipes specific categories from your account. It will ask for confirmation before deleting.

```bash 
uv run tidal-sync clear playlists
uv run tidal-sync clear tracks
uv run tidal-sync clear all
```

* **🔋 Power User Tip:** Bypass the warning prompt for scripting using `--force` (`-f`).

### 5. Logout (`logout`)

Securely destroys your session token from your computer.
```bash
uv run tidal-sync logout
```

---

## 🔁 Workflow: How to Clone an Account

Want to move your entire library from `Account A` to `Account B`? I got you ;)

**1. Log into the Source account:**
```bash
uv run tidal-sync login -p source
```

**2. Log into the Destination account:**
```bash
uv run tidal-sync login -p dest
```

**3. Export everything from the Source:**
```bash
uv run tidal-sync export -p source -o ./account_transfer
```

**4.** (Optional) Wipe the Destination account clean:
```bash
uv run tidal-sync clear all -p dest
```

**5. Import everything into the Destination:**
```bash 
uv run tidal-sync import ./account_transfer -p dest
```

---

## 📄 CSV Format Supported

The CLI natively exports and accurately parses this structure:
```Code snippet
Track name,Artist name,Album,Playlist name,Type,ISRC,Tidal - id
"Under Pressure","My Chemical Romance","Under Pressure","My Favs","Playlist","USRE10500450","2124179"
```

_(Also supports legacy Exportify Track Name,Artist Name(s) formats)_

