#!/bin/sh
set -e

MAX_RETRIES=30
RETRY_COUNT=0

echo "Waiting for MinIO to be ready..."
until mc alias set myminio http://minio:9000 "${MINIO_ACCESS_KEY}" "${MINIO_SECRET_KEY}" > /dev/null 2>&1; do
  RETRY_COUNT=$((RETRY_COUNT + 1))
  if [ $RETRY_COUNT -ge $MAX_RETRIES ]; then
    echo "ERROR: MinIO not ready after ${MAX_RETRIES} attempts. Exiting."
    exit 1
  fi
  echo "MinIO is not ready yet, retrying in 2 seconds... (${RETRY_COUNT}/${MAX_RETRIES})"
  sleep 2
done

echo "MinIO is ready. Creating bucket: ${MINIO_BUCKET}"
mc mb --ignore-existing "myminio/${MINIO_BUCKET}"

echo "Setting bucket policy to allow downloads..."
mc anonymous set download "myminio/${MINIO_BUCKET}"

echo "MinIO initialization completed successfully."
exit 0
