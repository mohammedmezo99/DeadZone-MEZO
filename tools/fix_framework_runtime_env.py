#!/usr/bin/env python3
from pathlib import Path
import re

source = Path('.github/workflows/framework.yml')
out = Path('framework.runtime-fixed.yml')
text = source.read_text(encoding='utf-8')

pattern = re.compile(
    r'(      - name: ⚙️ Load central Framework runtime\n        id: runtime\n        env:\n          GH_TOKEN: \$\{\{ secrets\.GH_TOKEN \}\}\n          DEADZONE_CONTROLLED_BUILD: \$\{\{ env\.DZ_CONTROLLED \}\}\n        run: \|\n)(.*?)(?=\n      - name: 📡 Prepare signed DeadZone events\n)',
    re.S,
)
replacement = r'''\1          set -Eeuo pipefail
          for runtime_file in projects.env build.env rclone.conf load-build.sh bot.env; do
            [[ -s "runtime/${runtime_file}" ]] || { echo "::error::Missing central runtime file: ${runtime_file}"; exit 1; }
          done
          chmod 700 runtime/load-build.sh

          # Values written to GITHUB_ENV become visible to later steps only.
          # Source the trusted private runtime here as well because this step
          # must validate/mask the Telegram transport before continuing.
          set -a
          # shellcheck disable=SC1091
          source runtime/build.env
          # shellcheck disable=SC1091
          source runtime/bot.env
          set +a

          runtime/load-build.sh framework

          required=(BUILD_PROGRESS_SECRET TELEGRAM_BOT_TOKEN TELEGRAM_API_ID TELEGRAM_API_HASH CLOUD_CHANNEL_ID)
          for key in "${required[@]}"; do
            [[ -n "${!key:-}" ]] || { echo "::error::Central runtime value is missing: ${key}"; exit 1; }
          done
          [[ "$TELEGRAM_API_ID" =~ ^[0-9]+$ ]] || { echo "::error::Invalid TELEGRAM_API_ID"; exit 1; }
          [[ "$CLOUD_CHANNEL_ID" =~ ^-[0-9]+$ ]] || { echo "::error::Invalid CLOUD_CHANNEL_ID"; exit 1; }

          for key in BUILD_PROGRESS_SECRET TELEGRAM_BOT_TOKEN TELEGRAM_API_ID TELEGRAM_API_HASH CLOUD_CHANNEL_ID; do
            printf '::add-mask::%s\n' "${!key}"
          done
          {
            printf 'TELEGRAM_BOT_TOKEN=%s\n' "$TELEGRAM_BOT_TOKEN"
            printf 'TELEGRAM_API_ID=%s\n' "$TELEGRAM_API_ID"
            printf 'TELEGRAM_API_HASH=%s\n' "$TELEGRAM_API_HASH"
            printf 'CLOUD_CHANNEL_ID=%s\n' "$CLOUD_CHANNEL_ID"
            printf 'DZ_EVENT_SECRET=%s\n' "$BUILD_PROGRESS_SECRET"
          } >> "$GITHUB_ENV"
'''
text, count = pattern.subn(replacement, text, count=1)
if count != 1:
    raise SystemExit(f'central runtime block target count={count}')

if 'required=(DEADZONE_ENGINE_REPOSITORY' in text:
    raise SystemExit('same-step core env validation still present')
if 'source runtime/bot.env' not in text or 'DZ_EVENT_SECRET=%s' not in text:
    raise SystemExit('runtime fix markers missing')

out.write_text(text, encoding='utf-8')
print(f'generated {out}')
