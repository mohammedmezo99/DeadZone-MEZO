# DeadZone-MEZO

GitHub Actions launcher for the private `mohammedmezo99/DeadZone-xiaomi_Lite` ROM builder.

This repository intentionally contains only:

- `.github/workflows/build.yml`
- this README

The workflow checks out the private builder at runtime, then runs its existing pipeline:

```text
setup.sh -> build.sh -> packROM.sh -> uploadROM.sh
```

## Run a build

1. Open **Actions**.
2. Select **DeadZone MEZO ROM Launcher**.
3. Select **Run workflow**.
4. Enter the ROM download URL.
5. Optionally enter the builder name and Telegram user ID.

## Required Actions secrets

Add these under **Settings -> Secrets and variables -> Actions**:

| Secret | Purpose |
|---|---|
| `GH_TOKEN` | Fine-grained token with read access to `DeadZone-xiaomi_Lite` and the repository that stores the Rclone config |
| `GH_REPO` | Repository containing the Rclone config, in `owner/repository` format |
| `RCLONE_TOKEN_PATH` | Path to the Rclone config file inside `GH_REPO` |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token used by `notify.py` |
| `TELEGRAM_CHANNEL_ID` | Telegram channel or chat ID used by `notify.py` |

Do not commit tokens, Rclone credentials, private source files, or generated ROM files to this repository.
