#!/usr/bin/env bash
# Build the two Lambda artifacts consumed by Terraform:
#   build/layer.zip  third-party deps for python3.14 / arm64 (manylinux wheels only)
#   build/app.zip    our package + alembic migrations, package dir at the zip root
# boto3/botocore are excluded: the Lambda runtime ships them.
set -euo pipefail
cd "$(dirname "$0")/.."

rm -rf build && mkdir -p build/layer/python build/app

uv export --no-dev --no-hashes --no-emit-project --no-emit-package boto3 --no-emit-package botocore --no-emit-package greenlet \
  -o build/requirements.txt

uv pip install \
  --requirements build/requirements.txt \
  --target build/layer/python \
  --python-platform aarch64-manylinux_2_28 \
  --python-version 3.14 \
  --only-binary :all: \
  --no-compile-bytecode \
  --quiet

# Trim what Lambda never needs.
find build/layer/python -type d \( -name '__pycache__' -o -name 'tests' -o -name '*.dist-info' \) \
  -prune -exec rm -rf {} +

(cd build/layer && zip -qr ../layer.zip python)

cp -r src/pricepulse build/app/pricepulse
cp -r alembic build/app/alembic
cp alembic.ini build/app/alembic.ini
find build/app -name '__pycache__' -type d -prune -exec rm -rf {} +
(cd build/app && zip -qr ../app.zip .)

du -h build/layer.zip build/app.zip
