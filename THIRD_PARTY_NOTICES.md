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
| click | (transitive of typer historically) | BSD-3-Clause | https://github.com/pallets/click/blob/main/LICENSE.txt |
| loguru | >=0.7.3 | MIT | https://github.com/Delgan/loguru/blob/master/LICENSE |
| orjson | >=3.11.9 | MPL-2.0 AND (Apache-2.0 OR MIT) | https://github.com/ijl/orjson/blob/master/LICENSE |
| pydantic | >=2.12.2 | MIT | https://github.com/pydantic/pydantic/blob/main/LICENSE |
| requests | >=2.32.0 | Apache-2.0 | https://github.com/psf/requests/blob/main/LICENSE |
| rich | >=14.2.0 | MIT | https://github.com/Textualize/rich/blob/master/LICENSE |
| tidalapi | >=0.8.9 | LGPL-3.0-or-later | https://github.com/EbbLabs/python-tidal/blob/main/LICENSE |
| typer | >=0.12.1 | MIT | https://github.com/fastapi/typer/blob/master/LICENSE |

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

- MIT, BSD-3-Clause, and LGPL-3.0-or-later packages permit redistribution provided
  the copyright notice and licence text are retained. tidal-sync depends on
  tidalapi (LGPL-3.0-or-later); the AGPL-3.0 licence of tidal-sync is compatible
  with linking against an LGPL library, and tidal-sync does not modify tidalapi.
  This summary, together with each package's own licence file in the installed
  environment, satisfies the attribution requirement.
- orjson is distributed under MPL-2.0 AND (Apache-2.0 OR MIT); tidal-sync links
  against orjson without modification, so the unmodified distribution term of
  each licence applies. requests (Apache-2.0) additionally grants a patent
  licence and requires that modifications be stated; tidal-sync does not
  modify requests.

This table is regenerated from installed package metadata. When a dependency
is added, removed, or relicensed, refresh it from `importlib.metadata` and
re-check the shipped wheel's contents.
