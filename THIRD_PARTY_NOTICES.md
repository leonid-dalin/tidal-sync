# Third-party notices

tidal-sync is licensed under the GNU Affero General Public License version 3
(AGPL-3.0). It bundles and depends on several third-party Python packages, each
distributed under its own licence. This file is a convenience summary; the
authoritative licence texts ship inside each package's own distribution (for
example `site-packages/<package>/LICENSE` after installation) and override
anything summarised here.

The project links to these libraries at runtime:

| Package | Version constraint | Licence | Upstream licence file |
| --- | --- | --- | --- |
| tidalapi | >=0.8.9 | MIT | https://github.com/tidalapi/tidalapi/blob/master/LICENSE |
| typer | >=0.12.1 | MIT | https://github.com/fastapi/typer/blob/master/LICENSE |
| click | (dependency of typer) | BSD-3-Clause | https://github.com/pallets/click/blob/main/LICENSE.txt |
| pydantic | >=2.12.2 | MIT | https://github.com/pydantic/pydantic/blob/main/LICENSE |
| rich | >=14.2.0 | MIT | https://github.com/Textualize/rich/blob/master/LICENSE |
| orjson | >=3.11.9 | Apache-2.0 | https://github.com/ijl/orjson/blob/master/LICENSE-APACHE |
| loguru | >=0.7.3 | MIT | https://github.com/Delgan/loguru/blob/master/LICENSE |

Development and build tooling (not shipped in the wheel but used to test and
build tidal-sync) carries its own licences:

| Package | Version constraint | Licence |
| --- | --- | --- |
| pytest | >=8.3.0 | MIT |
| pytest-asyncio | >=0.24.0 | MIT |
| pytest-cov | >=6.0.0 | MIT |
| ruff | >=0.8.0 | MIT |
| mypy | >=1.13.0 | MIT |
| hatchling | (build backend) | MIT |

## AGPL-3.0 obligations

Because `tidal-sync` is distributed under AGPL-3.0, any conveyance of the software
must also convey the complete corresponding source and the licence texts of all
covered components. The full AGPL-3.0 text is in [LICENSE](LICENSE) at the
repository root. If you modify tidal-sync and offer it over a network, the AGPL
remote-network clause requires you to offer the modified source to users who
interact with it.

## Notes on individual licences

- MIT and BSD-3-Clause packages permit redistribution provided the copyright
  notice and licence text are retained. This summary, together with each
  package's own licence file in the installed environment, satisfies that
  requirement.
- Apache-2.0 (orjson) additionally grants a patent licence and requires that
  modifications be stated; tidal-sync does not modify orjson, so the unmodified
  distribution term applies.

This document is maintained by hand. When a dependency is added, removed, or
relicensed, update the tables above and re-check the shipped wheel's contents.
