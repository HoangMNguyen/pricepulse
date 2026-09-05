#!/usr/bin/env bash
# Build the two Lambda artifacts consumed by Terraform:
#   build/layer.zip  third-party deps for python3.14 / arm64 (manylinux wheels only)
#   build/app.zip    our package + alembic migrations, package dir at the zip root
# boto3/botocore/s3transfer are excluded: the Lambda runtime ships them. The export is the full
# dependency closure, so the install runs --no-deps (otherwise pip re-resolves and pulls botocore
# back in through s3transfer). Fixed mtimes + sorted entries make the zips byte-identical for
# identical inputs, so Terraform's source_code_hash only changes when code changes.
set -euo pipefail
cd "$(dirname "$0")/.."

rm -rf build && mkdir -p build/layer/python build/app

uv export --quiet --no-dev --no-hashes --no-emit-project \
  --no-emit-package boto3 --no-emit-package botocore --no-emit-package s3transfer --no-emit-package greenlet \
  -o build/requirements.txt

uv pip install \
  --no-deps \
  --requirements build/requirements.txt \
  --target build/layer/python \
  --python-platform aarch64-manylinux_2_28 \
  --python-version 3.14 \
  --only-binary :all: \
  --no-compile-bytecode \
  --quiet

# Trim what Lambda never needs. Keep *.dist-info: psycopg locates psycopg_binary via importlib.metadata.
find build/layer/python -type d \( -name '__pycache__' -o -name 'tests' \) -prune -exec rm -rf {} +

cp -r src/pricepulse build/app/pricepulse
cp -r alembic build/app/alembic
cp alembic.ini build/app/alembic.ini
find build/app -name '__pycache__' -type d -prune -exec rm -rf {} +

find build/layer build/app -exec touch -h -d '2000-01-01T00:00:00Z' {} +
(cd build/layer && find python -type f | LC_ALL=C sort | zip -X -q ../layer.zip -@)
(cd build/app && find . -type f | LC_ALL=C sort | zip -X -q ../app.zip -@)

layer=$(stat -c %s build/layer.zip)
((layer < 50 * 1024 * 1024)) || {
  echo "layer.zip exceeds the 50 MB direct-upload limit" >&2
  exit 1
}
du -h build/layer.zip build/app.zip
