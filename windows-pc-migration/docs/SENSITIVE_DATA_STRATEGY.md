# SENSITIVE_DATA_STRATEGY (pre-cutover)

Rule: nothing below is ever copied into the unencrypted D: package. Paths are in a LOCAL-only list (`inventory_out/sensitive_paths_LOCAL_ONLY.txt`); only counts are published.

Excluded by current rules and found on C: (Desktop/Documents/Downloads, excluding node_modules/.venv): **26 files** = 3 `.env`, 4 `.pem`, 4 `.key`, 15 `.pfx`; **3** `browser_profile(s)` directories. No `~/.ssh`, no password DB (.kdbx).

| Class | Items | Handling |
|---|---|---|
| A. Recreate / re-issue / re-authenticate (default) | `.env` (3) -> re-create from the service owner / secret manager; browser sessions & cookies (Chrome Default, Edge Default) -> re-login; GitHub CLI token, Claude Code, Codex, MongoDB Compass connections, VS Code sign-in, Docker sign-in, LINE, Teams/M365 | Re-login on new PC. Never copy tokens/cookies. |
| B. Approved encrypted transfer only | `.pfx` (15), `.pem/.key` (8) that are still needed; MongoDB Compass saved connections (contain credentials) | Only if still needed: Owner decides per file; use a company-approved encrypted channel (BitLocker To Go volume / company vault) or, better, re-issue the key. |
| C. Do not migrate | Machine certificate from webcomm-N2-02-CA (device-bound, expires 2026-12-30), BitLocker recovery keys, CrowdStrike/FortiClient/OETH state, domain identity, TPM-bound keys, drivers, device licences | IT re-enrolls the new PC. |

Owner action before cutover: classify the `.pfx` and `.pem/.key` files as A/B/C (list is local). Unclassified = treated as A (not migrated).
D: package is unencrypted: enable BitLocker To Go on D: (or keep it physically secured) before the final delta puts newer data on it.
