#!/bin/bash
# Usage: ./setup-state-bucket.sh [environment]
# environment defaults to 'development'

# 0. Set variables
PROJECT_NAME="agentic-rag-ecommerce"
ENVIRONMENT="${1:-development}"
REGION="ap-southeast-1"
BUCKET_NAME="${PROJECT_NAME}-${ENVIRONMENT}-terraform-state"

echo "Creating Terraform state bucket: ${BUCKET_NAME} in ${REGION}"

# 1. Create S3 Bucket
aws s3api create-bucket \
    --bucket "${BUCKET_NAME}" \
    --region "${REGION}" \
    --create-bucket-configuration LocationConstraint="${REGION}" \
    --no-cli-pager

# 2. Enable Versioning
aws s3api put-bucket-versioning \
    --bucket "${BUCKET_NAME}" \
    --versioning-configuration Status=Enabled

# 3. Enable Server-Side Encryption
aws s3api put-bucket-encryption \
    --bucket "${BUCKET_NAME}" \
    --server-side-encryption-configuration '{"Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]}'

# 4. Block Public Access
aws s3api put-public-access-block \
    --bucket "${BUCKET_NAME}" \
    --public-access-block-configuration "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"

echo "Done. Bucket name to use in backend.tf: ${BUCKET_NAME}"