#!/usr/bin/env bash
# 打包上线阿里云用的部署包,自动排除敏感文件(.env / .git / 数据库等)
set -euo pipefail

cd "$(dirname "$0")"

OUT="writer.tar.gz"

tar --exclude="./.env" \
    --exclude="./.git" \
    --exclude="./.venv" \
    --exclude="./.pytest_cache" \
    --exclude="./__pycache__" \
    --exclude="./chroma_data" \
    --exclude="./output" \
    --exclude="./dump.rdb" \
    --exclude="*.db" \
    --exclude="*.db-shm" \
    --exclude="*.db-wal" \
    --exclude="*.pyc" \
    --exclude="./.coverage" \
    --exclude="./$OUT" \
    -czf "$OUT" . || [ "$?" -eq 1 ]  # tar 退出码 1 = 打包中有文件被改动(如 dump.rdb),无害

echo "打包完成: $OUT ($(du -h "$OUT" | cut -f1))"
echo "安全检查(下面应为空):"
tar -tzf "$OUT" | grep -iE "(^|/)\.env$|(^|/)\.git/|\.db$|dump\.rdb" && echo "⚠️  发现敏感文件,请勿上传!" || echo "✓ 无敏感文件,可安全上传"
