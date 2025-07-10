#!/usr/bin/env bash

# download large file from google drive
set -euo pipefail

FILEID="file_id"
FILENAME="file_name"

# 1. get confirm token and uuid
wget --quiet \
     --save-cookies /tmp/gcookies.txt \
     --keep-session-cookies \
     --no-check-certificate \
     "https://docs.google.com/uc?export=download&id=${FILEID}" \
     -O /tmp/gdrive_page.html

CONFIRM=$(sed -rn 's/.*name="confirm" value="([^"]+)".*/\1/p' /tmp/gdrive_page.html)
UUID=$(sed -rn 's/.*name="uuid" value="([^"]+)".*/\1/p' /tmp/gdrive_page.html)

if [[ -z "$CONFIRM" || -z "$UUID" ]]; then
  echo "⚠️ cannot extract confirm or uuid, download failed." >&2
  exit 1
fi

echo "Got confirm=${CONFIRM}, uuid=${UUID}"

# 2. download the file
wget --load-cookies /tmp/gcookies.txt \
     --no-check-certificate \
     "https://drive.usercontent.google.com/download?export=download&id=${FILEID}&confirm=${CONFIRM}&uuid=${UUID}" \
     -O "${FILENAME}"

# 3. clean up
rm -f /tmp/gcookies.txt /tmp/gdrive_page.html

echo "downloaded ${FILENAME}"
