#!/bin/bash
# Usage: ./setup-lock-table.sh [environment]
# environment defaults to 'development'

# 0. Set variables
PROJECT_NAME="agentic-rag-ecommerce"
ENVIRONMENT="${1:-development}"
REGION="ap-southeast-1"
TABLE_NAME="${PROJECT_NAME}-${ENVIRONMENT}-terraform-lock-table"

echo "Creating Terraform lock table: ${TABLE_NAME} in ${REGION}"

# 1. Create DynamoDB Table
aws dynamodb create-table \
    --table-name "${TABLE_NAME}" \
    --attribute-definitions \
        AttributeName=LockID,AttributeType=S \
    --key-schema \
        AttributeName=LockID,KeyType=HASH \
    --billing-mode PAY_PER_REQUEST \
    --region "${REGION}" \
    --no-cli-pager

echo "Done. Table name to use in backend.tf: ${TABLE_NAME}"