#!/usr/bin/env python3
from pathlib import Path

source = Path('.github/workflows/framework.yml')
out = Path('framework.syntax-fixed.yml')
text = source.read_text(encoding='utf-8')

start_marker = '          for key in BUILD_PROGRESS_SECRET TELEGRAM_BOT_TOKEN TELEGRAM_API_ID TELEGRAM_API_HASH CLOUD_CHANNEL_ID; do\n'
end_marker = '          } >> "$GITHUB_ENV"\n'
start = text.find(start_marker)
if start < 0:
    raise SystemExit('mask/export block start not found')
end = text.find(end_marker, start)
if end < 0:
    raise SystemExit('mask/export block end not found')
end += len(end_marker)

safe = '''          for key in BUILD_PROGRESS_SECRET TELEGRAM_BOT_TOKEN TELEGRAM_API_ID TELEGRAM_API_HASH CLOUD_CHANNEL_ID; do
            echo "::add-mask::${!key}"
          done
          {
            echo "TELEGRAM_BOT_TOKEN=$TELEGRAM_BOT_TOKEN"
            echo "TELEGRAM_API_ID=$TELEGRAM_API_ID"
            echo "TELEGRAM_API_HASH=$TELEGRAM_API_HASH"
            echo "CLOUD_CHANNEL_ID=$CLOUD_CHANNEL_ID"
            echo "DZ_EVENT_SECRET=$BUILD_PROGRESS_SECRET"
          } >> "$GITHUB_ENV"
'''
text = text[:start] + safe + text[end:]
out.write_text(text, encoding='utf-8')
print(f'generated {out}')
