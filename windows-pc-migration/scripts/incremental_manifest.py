import os, sys, hashlib, datetime as dt, shutil
from concurrent.futures import ThreadPoolExecutor
R = r'D:\Windows_Migration_Package'
CK = os.path.join(R, '04_checksums')
sums_p, list_p = os.path.join(CK, 'SHA256SUMS.txt'), os.path.join(CK, 'FILELIST.tsv')
def L(p): return '\\\\?\\' + p
sums = {}; meta = {}
for ln in open(sums_p, encoding='utf-8-sig'):
    ln = ln.rstrip('\r\n')
    if not ln: continue
    h, rel = ln.split('  ', 1); sums[rel] = h
for ln in open(list_p, encoding='utf-8-sig'):
    ln = ln.rstrip('\r\n')
    if not ln: continue
    sz, mt, rel = ln.split('\t', 2); meta[rel] = (int(sz), dt.datetime.fromisoformat(mt.replace('Z', '+00:00')).timestamp() if mt.endswith('Z') else dt.datetime.fromisoformat(mt).replace(tzinfo=dt.timezone.utc).timestamp())
assert len(sums) == len(meta) >= 361390, (len(sums), len(meta))
changed = []; seen = set(); errors = 0
for d, ds, fs in os.walk(L(R)):
    rd = d[4:]
    if rd.startswith(CK) or rd.startswith(os.path.join(R, '05_logs')):
        ds[:] = []; continue
    for f in fs:
        full = os.path.join(rd, f); rel = full[len(R) + 1:]
        if rel in ('README_MIGRATION.md', r'00_inventory\backup_meta.json'): continue
        try: st = os.stat(L(full))
        except OSError: errors += 1; print('stat fail', rel); continue
        seen.add(rel)
        m = meta.get(rel)
        if m is None or m[0] != st.st_size or abs(m[1] - st.st_mtime) > 2: changed.append((rel, full, st))
missing = [r for r in sums if r not in seen]
print('changed/new:', len(changed), 'missing from disk:', len(missing), 'stat errors:', errors)
if missing or errors: sys.exit('ABORT: package lost files / unreadable: ' + str(missing[:5]))
def h(item):
    rel, full, st = item; x = hashlib.sha256()
    with open(L(full), 'rb') as fh:
        for b in iter(lambda: fh.read(1 << 20), b''): x.update(b)
    return rel, x.hexdigest().upper(), st
with ThreadPoolExecutor(8) as ex: res = list(ex.map(h, changed))
newc = sum(1 for r in res if r[0] not in sums)
for rel, hx, st in res:
    sums[rel] = hx; meta[rel] = (st.st_size, st.st_mtime)
print('new entries:', newc, 'updated entries:', len(res) - newc)
stamp = dt.datetime.now().strftime('%Y%m%d_%H%M%S')
shutil.copy2(sums_p, sums_p + '.prev_' + stamp); shutil.copy2(list_p, list_p + '.prev_' + stamp)
rels = sorted(sums)
with open(sums_p + '.tmp', 'w', encoding='utf-8-sig', newline='') as o:
    for r in rels: o.write(f'{sums[r]}  {r}\r\n')
with open(list_p + '.tmp', 'w', encoding='utf-8-sig', newline='') as o:
    for r in rels:
        sz, mt = meta[r]; o.write(f"{sz}\t{dt.datetime.fromtimestamp(mt, dt.timezone.utc).isoformat()}\t{r}\r\n")
os.replace(sums_p + '.tmp', sums_p); os.replace(list_p + '.tmp', list_p)
print('manifest entries now:', len(rels))
open(os.path.join(R, '05_logs', f'incremental_manifest_{stamp}.log'), 'w', encoding='utf-8').write(f'new={newc} updated={len(res)-newc} total={len(rels)}\n' + '\n'.join(r[0] for r in res) + '\n')
