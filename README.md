```text
████████╗██╗ ██████╗  █████╗ ██╗             ███████╗██╗   ██╗███╗   ██╗ ██████╗ 
╚══██╔══╝██║ ██╔══██╗██╔══██╗██║             ██╔════╝╚██╗ ██╔╝████╗  ██║██╔════╝ 
   ██║   ██║ ██║  ██║███████║██║      █████╗ ███████╗ ╚████╔╝ ██╔██╗ ██║██║      
   ██║   ██║ ██║  ██║██╔══██║██║      ╚════╝ ╚════██║  ╚██╔╝  ██║╚██╗██║██║      
   ██║   ██║ ██████╔╝██║  ██║███████╗        ███████║   ██║   ██║ ╚████║╚██████╗ 
   ╚═╝   ╚═╝ ╚═════╝ ╚═╝  ╚═╝╚══════╝        ╚══════╝   ╚═╝   ╚═╝  ╚═══╝ ╚═════╝
```

# 🌊 Tidal Sync `CLI`

A command-line tool that exports and imports Tidal music libraries. It matches tracks using exact Tidal IDs and ISRC codes, falling back to text search only when direct metadata is missing.

## Documentation

All guides and technical references are located in the `docs/` directory:

* **[Getting Started](docs/getting-started.md)**: Installation, authentication, and basic usage examples (exporting, importing, cloning).
* **[CLI Reference](docs/cli-reference.md)**: Exhaustive list of commands, flags, and arguments.
* **[Architecture](docs/architecture.md)**: Overview of the core routing, state management, and synchronisation modules.
* **[Data Flow](docs/data-flow.md)**: Step-by-step trace of how a track moves from a local CSV file to the Tidal servers.
* **[Telemetry](docs/telemetry.md)**: How the JSONL audit logging system works and how to read the output.

---

## ✨ Key Features
* Backs up your **custom playlists, liked songs, saved albums,** and **followed artists** to local CSV files.
* Scans your destination playlist before importing. If you already own a track, the tool skips it.
* Point the tool at a backup directory, and it automatically finds and imports every CSV file inside.
* Log into multiple accounts at once to easily clone data from a "Source" account to a "Destination" account. 
* Uses direct `Tidal IDs` and `ISRC` codes to match tracks. It only falls back to text search if the metadata is missing.
* Uses a thread pool to match and upload tracks in parallel, dropping import times from minutes to seconds.
* If a batch upload fails because Tidal region-locks a specific track, the tool recursively bisects the batch to isolate and drop the broken track, uploading the rest successfully.
* Generates machine-readable JSONL reports in the background so you know exactly which tracks were added, skipped, or failed.
* Performs an IEEE 2883-style logical zero-fill overwrite on your session tokens before deleting them, preventing standard disk data recovery.

---

## ⚖️ Licence & Open Source

This project is licensed under the **GNU Affero General Public Licence v3.0 (AGPLv3)**. 

This licence ensures that the software remains free for the public. Crucially, the **Affero** clause dictates that any person or entity using this code to provide a service over a network (such as a hypothetical web-based "Tidal Migration" service) **must** make their full source code available to the community.

The licence summaries for the third-party packages tidal-sync bundles are in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

### 🛑 Additional Terms & AI Restriction
**Closed-source commercialisation** of this work is strictly prohibited. Furthermore, I explicitly **withhold consent** for any content in this repository—including, but not limited to code, documentation, and logic—to be used as training data for artificial intelligence (AI) models, large language models (LLMs), or any generative systems. Automated remixing, adaptation, scraping, or building upon this work by AI entities without my explicit written permission is strictly prohibited. `:)`

---

## ⚠️ Disclaimer & Liability

**`tidal-sync` is an independent, open-source educational tool and is NOT affiliated with, endorsed by, or in any way associated with TIDAL Music AS.**

By using this tool, you agree to the following:
1. **Your Responsibilities:** You are solely responsible for how you use this software. This tool interacts with Tidal's API using standard user credentials. It is your responsibility to ensure your usage complies with [Tidal's Terms and Conditions of Use](https://tidal.com/terms).
2. **Account Risks:** Automated interactions with API endpoints can sometimes be flagged by anti-bot or abuse-prevention systems. The author(s) of `tidal-sync` hold **zero liability** for any account warnings, suspensions, bans, or data loss that may occur as a result of using this tool.
3. **No Warranty:** This software is provided "as is", without warranty of any kind. The author(s) shall not be liable for any claims, damages, or legal repercussions arising from the use of this software. 

**Use at your own risk.**