# DATA_INVENTORY (metadata only)

Backup estimate from migration.ps1 dry-run on the real profile (rebuildable dirs and sensitive files excluded):
**76.22 GB, ~342k files. Destination needs >= ~82 GB free.**

| Folder | Files | Size |
|---|---|---|
| Desktop | 341,225 | 75.66 GB (4 big top-level folders: ~46 GB, ~12.4 GB, ~11.9 GB, ~3.7 GB; the rest small) |
| Downloads | 1,568 | 0.56 GB |
| Documents / Pictures / Videos / Music | 7 / 1 / 2 / 1 | ~0 |
| OneDrive | 0 | not in use |

Projects: 81 project directories found (37 git repos; 26 with package.json, 6 pyproject, 4 docker compose). Most live under Desktop, so they are covered by the Desktop copy. Git repos with a remote can alternatively be re-cloned.
Excluded by default (rebuild): node_modules, .venv/venv, __pycache__, .cache, site-packages (~35k files, ~0.9 GB).
