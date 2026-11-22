# NEW PC RESTORE RUNBOOK（給新電腦上的 Claude Code 執行）

> 給 Claude Code：這份文件是 Owner 交給你的執行指示。請從頭到尾讀完再開始，依序執行，並遵守「停止點」。
> **不要修改、移動或刪除這份文件，也不要修改 `D:\Windows_Migration_Package` 內任何既有檔案**（package 已有 SHA-256 manifest，任何變更都會讓驗證失敗）。所有你自己的筆記、log 都寫到 `C:\Users\<使用者>\Desktop\migration_restore_logs\`。

---

## 0. 背景（只需了解，不需執行）

- 來源：舊電腦 NB0250（Lenovo 21C1S00Q00，Windows 11 Pro，網域 webcomm）。
- Package：`D:\Windows_Migration_Package`，在 Transcend StoreJet USB 外接硬碟（exFAT，約 1.81 TB，磁碟標籤 `Transcend`）。
- 最終狀態（2026-11-22）：**SAFE TO RESTORE**。361,562 個檔案（含本文件）、約 79.8 GB；完整 verify mismatch 0 / missing 0。
- 結構：
  - `00_inventory\` 舊電腦盤點（`final_delta_20261121\` 為最終盤點）
  - `01_backup\UserData\{Desktop,Documents,Downloads,Pictures,Videos,Music}` 使用者資料
  - `01_backup\AppSettings\` winget 清單、VS Code、pip、npm 清單；`AppSettings\Extra\` 為 Claude Code / Codex / MobaXterm / Obsidian / Edge 書籤設定
  - `01_backup\ScheduledTasks\` 排程工作 XML（預設**不**還原）
  - `02_restore\migration.ps1` 還原腳本
  - `04_checksums\SHA256SUMS.txt` manifest；`05_logs\` 備份 / 驗證 log
- 刻意**沒有**備份（不要去找，也不要嘗試從舊電腦複製）：`.env`、`*.pem/*.key/*.pfx/*.p12`、SSH 私鑰、密碼庫、瀏覽器 cookies / sessions / `browser_profile(s)`、`node_modules`、`.venv` 等可重建目錄、公司裝置憑證、BitLocker 金鑰、VPN / MFA 綁定。

## 停止點（遇到以下情況必須停下來，回報 Owner 並等待指示）

1. 找不到標籤為 `Transcend` 的磁碟，或有多顆可能的外接硬碟無法確定。
2. verify 結果不是 `mismatch=0 missing=0 errors=0`。
3. 需要 UAC 以外的密碼、MFA、公司 IT 權限、VPN、裝置註冊、憑證核發。
4. 任何會刪除或覆蓋新電腦既有資料的動作（`restore` 本身使用 `/XO`，不覆蓋較新的檔案，這是允許的）。
5. 執行正式 restore（第 5 步）之前：**先把第 3、4 步結果整理給 Owner，取得同意後才執行**。
6. 不要執行 `rollback`，除非 Owner 明確要求。
7. 不要動 MDM / EDR（CrowdStrike、Defender、F-Secure）、BitLocker、網域、驅動程式、FortiClient VPN、WebComm OETH。

---

## 1. 前置確認

1. 確認目前使用者與電腦名稱：`whoami`、`hostname`、`$env:USERPROFILE`。記下使用者目錄（如果不是 `C:\Users\devon.tseng`，後面所有路徑請自行替換）。
2. 找出 Transcend 磁碟代號：
   ```powershell
   Get-Volume | Where-Object DriveLetter | Format-Table DriveLetter, FileSystemLabel, FileSystem, HealthStatus, Size, SizeRemaining
   Get-Disk | Format-Table Number, FriendlyName, BusType, HealthStatus
   fsutil dirty query D:
   ```
   必須是：標籤 `Transcend`、exFAT、約 1863 GB、FriendlyName `StoreJet`、Healthy、NOT Dirty。若代號不是 `D:`，以下指令全部改用實際代號（以下以 `D:` 表示）。
3. 確認 `D:\Windows_Migration_Package\02_restore\migration.ps1` 與 `D:\Windows_Migration_Package\04_checksums\SHA256SUMS.txt` 存在。
4. 確認新電腦 C: 剩餘空間至少 **100 GB**。
5. 建立 log 目錄 `C:\Users\<使用者>\Desktop\migration_restore_logs\`。

## 2. 準備執行環境

- 用 **系統管理員 PowerShell（Windows PowerShell 5.1）**。如果你（Claude Code）不是以系統管理員執行，第 3、4 步可以非管理員執行；第 5 步需要管理員，請用 `Start-Process powershell -Verb RunAs` 觸發 UAC，Owner 只會在 Windows 跳出 UAC 時按「是」。**不要用 PID 判斷成功，一律用輸出 log 判斷。**
- 長時間指令（verify 約 1.5 小時）請放到背景執行，並把輸出導到檔案再追蹤，不要用前景等待。
- 執行原則：
  ```powershell
  Set-ExecutionPolicy -Scope Process Bypass -Force
  $P = 'D:\Windows_Migration_Package'
  $S = "$P\02_restore\migration.ps1"
  ```
  注意：verify / restore 會把 log 寫到 `D:\Windows_Migration_Package\05_logs\`（這是允許的，`05_logs` 不在 manifest 內）。

## 3. Pre-Restore Verify（唯讀，約 1.5 小時）

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File $S verify -Source $P
```
- 成功條件：`verify: manifest entries=N` 與 `package verify: ok=N mismatch=0 missing=0`（N 目前為 361562，ok 必須等於 manifest entries）、`files in package but not in manifest: 0`、`DONE action=verify errors=0`。
- 應該會看到 `source-vs-package comparison SKIPPED: this is not the backup host`（正常，新電腦不比對舊來源）。如果電腦名稱剛好也是 `NB0250`，請改加 `-SkipSourceCompare` 參數重跑。
- 結尾的 `winget packages not yet installed here: N of M` 只是資訊，不是錯誤。
- 若 mismatch / missing 不為 0：**停止點 2**。逐筆列出 log 中的檔案，不要自行修補。

## 4. Restore Dry Run（不會改任何東西）

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File $S restore -Source $P -DryRun
```
- Dry run 也會先跑一次完整 verify（約 1.5 小時）。
- Dry run 的 log 在 `%TEMP%\migration_restore_*.log`。整理：每個資料夾預計還原的檔案數 / 大小、winget import、VS Code 擴充、設定檔、被設計跳過的項目。
- **停止點 5**：把第 3、4 步結果整理給 Owner，等 Owner 同意。

## 5. 正式 Restore（需要系統管理員，Owner 同意後才執行）

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File $S restore -Source $P
```
- 會再自動跑一次 verify（約 1.5 小時），通過後才會還原；不通過會拒絕還原。
- 使用者資料用 robocopy `/E /XO` 還原到 `%USERPROFILE%\{Desktop,Documents,...}`，新電腦上較新的檔案不會被覆蓋。新建立的檔案清單會記在 `05_logs\restore_created.txt`（供日後 rollback 使用）。
- 之後執行 `winget import`（已安裝的會跳過）、安裝 VS Code 擴充、還原 VS Code settings.json、`.gitconfig`（舊電腦沒有，會跳過）。
- 排程工作**不還原**（AI_CUP_* 是另一個專案，Owner 未要求前不要用 `-RestoreTasks`）。
- 成功條件：`DONE action=restore errors=0`。robocopy rc < 8 都算成功。

## 6. 補裝工具（Restore 後）

`winget export` 在舊電腦失敗，package 內 `AppSettings\winget_export.json` 可能較舊；以 `D:\Windows_Migration_Package\00_inventory\final_delta_20261121\winget_export_curated.json` 為準，補裝缺少的套件（已安裝就跳過）：
```powershell
winget import -i "$P\00_inventory\final_delta_20261121\winget_export_curated.json" --accept-package-agreements --accept-source-agreements --ignore-unavailable
```
清單：7zip、Adobe Reader、CopyTrans Studio、DBeaver、PotPlayer、Docker Desktop、Git、GitHub CLI、Lenovo QuickClean、M365 Copilot、Outlook、Teams、VS Code、MobaXterm、MongoDB Compass、Obsidian、Node.js LTS、Python Launcher、Python 3.13。
另外：
- npm 全域：`npm i -g @anthropic-ai/claude-code @openai/codex ccusage`
- pip：`$P\01_backup\AppSettings\pip_freeze.txt` 可用 `python -m pip install -r` 安裝（若有版本衝突，回報 Owner，不要強制）。
- 不在 winget 的（LINE、FortiClient、CrowdStrike、WebComm OETH、M365 Apps、Septerra Core、OpenSSL Light）：只列給 Owner，公司軟體由 IT 安裝。

## 7. 還原 Extra 設定（`01_backup\AppSettings\Extra\`）

每一項：若目的地已存在檔案，**先備份成 `.bak_newpc` 再覆蓋**，並記錄在你的 log。
| 來源 | 目的地 |
|---|---|
| `Extra\MobaXterm\MobaXterm.ini` | `%APPDATA%\MobaXterm\MobaXterm.ini`（先開過一次 MobaXterm 讓資料夾建立） |
| `Extra\Obsidian\obsidian.json` | `%APPDATA%\obsidian\obsidian.json`；確認裡面的 vault 路徑存在（`Documents\Obsidian Vault` 已由 restore 還原） |
| `Extra\Browsers\Edge\Bookmarks` | 建議改用 Edge 帳號同步；若 Owner 要手動匯入，Edge 關閉時複製到 `%LOCALAPPDATA%\Microsoft\Edge\User Data\Default\Bookmarks` |
| `Extra\ClaudeCode\hooks\`、`skills\`、`agents\`、`commands\` | `%USERPROFILE%\.claude\` 對應資料夾 |
| `Extra\ClaudeCode\settings.json` | `%USERPROFILE%\.claude\settings.json`：**先檢查內容**，hooks 指令裡的路徑若含舊使用者目錄或 Python 路徑，需改成新電腦實際路徑；Python 未安裝前不要套用含 hooks 的設定 |
| `Extra\ClaudeCode\plugins\*.json` | 只作參考，用 Claude Code 的 plugin 指令重新安裝，不要直接覆蓋 |
| `Extra\Codex\config.toml`、`AGENTS.md`、`rules\`、`skills\` | `%USERPROFILE%\.codex\` |
| `Extra\VSCode\snippets\` | `%APPDATA%\Code\User\snippets\` |

Claude Code 對話同步到 GitHub（hooks 依賴本機 repo）：Owner 完成 `gh auth login` 後，執行
`gh repo clone DevonForlan/claude-conversation-logs "$env:USERPROFILE\.claude\cc-log-repo"`，再開一個新對話確認 hooks 有推送成功。

## 8. Post-Restore Validation

1. 檔案數抽查：比較 `D:\Windows_Migration_Package\01_backup\UserData\<資料夾>` 與 `%USERPROFILE%\<資料夾>` 的檔案數（新電腦可能多出原本就有的檔案，不能少）。
2. 抽樣 SHA-256：從 `SHA256SUMS.txt` 中 `01_backup\UserData\` 開頭的條目隨機抽 500 筆，計算新電腦上對應檔案的 SHA-256 比對（路徑超過 260 字元時要用 `\\?\` 前綴）。若因新電腦上檔案較新而不同，列出說明即可。
3. 確認：`git --version`、`gh --version`、`code --version`、`python --version`、`node --version`、`claude --version`、`codex --version`、`docker --version`。
4. 主要 Git repo（Desktop\個人 底下）執行 `git status` 抽查幾個，確認 repo 可讀。
5. 列出仍需 Owner 手動處理的 re-login 清單（見下一節）。

## 9. 需要 Owner 本人處理（你只負責列出與提醒）

- IT：網域 / 裝置註冊、FortiClient VPN 設定檔與 MFA、CrowdStrike / Defender、WebComm OETH 授權、公司機器憑證（舊憑證 2026-12-30 到期，裝置綁定，不能搬）。
- 登入：Chrome / Edge 帳號同步、`gh auth login`、git `user.name` / `user.email`、VS Code、Claude Code、Codex、Docker Desktop、MongoDB Compass / DBeaver / MobaXterm 連線與密碼、Microsoft 365 / Outlook / Teams、LINE、Obsidian。
- 敏感檔：`.env`（3 個）向服務擁有者重建；`.pfx`（15）/ `.pem`、`.key`（8）依 Owner 分類決定是否以核准的加密方式另外轉移，或重新核發。
- D: 未加密：搬遷驗收完成前保留 package；之後由 Owner 決定是否啟用 BitLocker To Go 或保管 / 清除。

## 10. 最後回報格式

```
NEW PC RESTORE STATUS
- Transcend 磁碟：代號 / Healthy / Dirty
- Pre-Restore Verify：ok / mismatch / missing / errors
- Dry Run：各資料夾預計檔案數
- Restore：errors / 各資料夾 robocopy rc / log 路徑
- winget / npm / pip / VS Code 擴充：成功與失敗清單
- Extra 設定：已還原 / 略過 / 需手動
- Post-Restore Validation：檔案數比較、SHA-256 抽樣結果、工具版本
- WARNING
- MANUAL ACTION（Owner）
- 結論：RESTORE COMPLETE / RESTORE INCOMPLETE（列出原因）
```
