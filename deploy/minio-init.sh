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

echo "MinIO is ready. Creating attachment bucket: ${MINIO_BUCKET}"
mc mb --ignore-existing "myminio/${MINIO_BUCKET}"

echo "Setting attachment bucket policy to allow downloads..."
mc anonymous set download "myminio/${MINIO_BUCKET}"

# Plan 06 private assistant Artifact bucket — NEVER assign anonymous policy.
# Durable Provider/Artifact content must not use the public attachment bucket.
ASSISTANT_ARTIFACT_BUCKET="${ASSISTANT_ARTIFACT_BUCKET:-mindatlas-assistant-artifacts}"
if [ -z "${ASSISTANT_ARTIFACT_BUCKET}" ]; then
  echo "ERROR: ASSISTANT_ARTIFACT_BUCKET is empty"
  exit 1
fi
if [ "${ASSISTANT_ARTIFACT_BUCKET}" = "${MINIO_BUCKET}" ]; then
  echo "ERROR: ASSISTANT_ARTIFACT_BUCKET must be distinct from MINIO_BUCKET"
  exit 1
fi

echo "Creating private assistant Artifact bucket: ${ASSISTANT_ARTIFACT_BUCKET}"
mc mb --ignore-existing "myminio/${ASSISTANT_ARTIFACT_BUCKET}"

# Explicitly clear any anonymous policy (private by default; belt-and-suspenders).
echo "Ensuring assistant Artifact bucket has no anonymous policy..."
mc anonymous set none "myminio/${ASSISTANT_ARTIFACT_BUCKET}" || true

echo "MinIO initialization completed successfully."
echo "Attachment bucket (public download): ${MINIO_BUCKET}"
echo "Assistant Artifact bucket (private): ${ASSISTANT_ARTIFACT_BUCKET}"
exit 0
