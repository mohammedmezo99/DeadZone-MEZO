from pathlib import Path

path = Path('.github/workflows/framework.yml')
text = path.read_text(encoding='utf-8')

text = text.replace(
    'description: 📦 Telegram storage message map (JSON)',
    'description: 📦 Telegram file ID map (JSON)',
)
text = text.replace(
    '''          for key, value in telegram_files.items():
              if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                  raise SystemExit(f"Invalid Telegram message ID for {key}")''',
    '''          for key, value in telegram_files.items():
              if not isinstance(value, str) or not value.strip():
                  raise SystemExit(f"Invalid Telegram file ID for {key}")''',
)
text = text.replace(
    'required=(BUILD_PROGRESS_SECRET TELEGRAM_BOT_TOKEN TELEGRAM_API_ID TELEGRAM_API_HASH CLOUD_CHANNEL_ID)',
    'required=(BUILD_PROGRESS_SECRET TELEGRAM_BOT_TOKEN TELEGRAM_API_ID TELEGRAM_API_HASH)',
)
text = text.replace(
    '          [[ "$CLOUD_CHANNEL_ID" =~ ^-[0-9]+$ ]] || { echo "::error::Invalid CLOUD_CHANNEL_ID"; exit 1; }\n',
    '',
)
text = text.replace(
    'for key in BUILD_PROGRESS_SECRET TELEGRAM_BOT_TOKEN TELEGRAM_API_ID TELEGRAM_API_HASH CLOUD_CHANNEL_ID; do',
    'for key in BUILD_PROGRESS_SECRET TELEGRAM_BOT_TOKEN TELEGRAM_API_ID TELEGRAM_API_HASH; do',
)
text = text.replace('            echo "CLOUD_CHANNEL_ID=$CLOUD_CHANNEL_ID"\n', '')
text = text.replace(
    'python3 -m pip install --user --break-system-packages "gdown==5.2.0" "telethon==1.44.0"',
    'python3 -m pip install --user --break-system-packages "gdown==5.2.0" "pyrofork==2.3.58" "tgcrypto>=1.2.5"',
)

start = text.find('      - name: 📥 Download Framework JARs from private Telegram storage')
end = text.find('      - name: 🚀 Run FrameworkPatcher engine', start)
if start < 0 or end < 0:
    raise SystemExit('legacy Framework Telegram download step not found')

step = '''      - name: 📥 Download Framework JARs from Telegram file IDs
        working-directory: engine
        env:
          TELEGRAM_FILES: ${{ inputs.telegram_files }}
        run: |
          set -Eeuo pipefail
          python3 - <<'PY'
          import asyncio
          import json
          import os
          import pathlib
          import zipfile

          from pyrogram import Client

          file_map = json.loads(os.environ["TELEGRAM_FILES"])
          inputs = {
              "framework.jar": "framework_url",
              "services.jar": "services_url",
              "miui-services.jar": "miui_services_url",
              "miui-framework.jar": "miui_framework_url",
          }

          async def main():
              app = Client(
                  "deadzone-framework-download",
                  api_id=int(os.environ["TELEGRAM_API_ID"]),
                  api_hash=os.environ["TELEGRAM_API_HASH"],
                  bot_token=os.environ["TELEGRAM_BOT_TOKEN"],
                  in_memory=True,
                  no_updates=True,
              )
              await app.start()
              try:
                  targets = []
                  for name, key in inputs.items():
                      file_id = file_map.get(key)
                      if not file_id:
                          continue
                      if not isinstance(file_id, str) or not file_id.strip():
                          raise RuntimeError(f"Invalid Telegram file ID: {name}")
                      target = pathlib.Path(name).resolve()
                      result = await app.download_media(file_id.strip(), file_name=str(target))
                      if not result or not target.is_file():
                          raise RuntimeError(f"Telegram file-ID download failed: {name}")
                      targets.append(target)

                  for target in targets:
                      size = target.stat().st_size
                      if size < 1_500_000 or size > 120 * 1024 * 1024 or not zipfile.is_zipfile(target):
                          raise RuntimeError(f"Invalid JAR input: {target.name}")
                      print(f"✅ {target.name} validated ({size} bytes)")
              finally:
                  await app.stop()

          asyncio.run(main())
          PY
          python3 "$RUNNER_TEMP/deadzone-framework-event.py" running 32 "Framework JAR inputs verified directly from Telegram." || true

'''
text = text[:start] + step + text[end:]
path.write_text(text, encoding='utf-8')
