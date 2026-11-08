# SECURITY_EXCLUSIONS

Never uploaded to GitHub: the package, raw inventory output, logs with paths, any file content.
Excluded from the package by default: `.env`, `.env.*`, `*.pem`, `*.ppk`, `*.pfx`, `*.p12`, `*.key`, `id_rsa`, `id_ed25519`, `id_ecdsa`, `credentials*`, `*.kdbx`.
`-IncludeSensitive` is refused unless the destination volume is confirmed BitLocker-protected.
Not copied at all: browser profiles/cookies, tokens, password stores, DPAPI blobs, certificate private keys, MDM/EDR/BitLocker/domain state, drivers.
BitLocker recovery keys are never read. SSH private keys: none found on this PC.
