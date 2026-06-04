# =============================================================================
# S3
# =============================================================================

output "s3_bucket_name" {
  description = "Name of the S3 media bucket. Set as AWS_S3_BUCKET in .env."
  value       = module.s3.bucket_name
}

output "s3_bucket_arn" {
  description = "ARN of the S3 media bucket."
  value       = module.s3.bucket_arn
}

# =============================================================================
# IAM
# =============================================================================

output "iam_user_name" {
  description = "Name of the IAM user."
  value       = module.iam.user_name
}

output "iam_access_key_id" {
  description = "IAM access key ID. Set as AWS_ACCESS_KEY_ID in .env. Also in SSM."
  value       = module.iam.access_key_id
  sensitive   = true
}

output "iam_secret_access_key" {
  description = "IAM secret access key. Set as AWS_SECRET_ACCESS_KEY in .env. Also in SSM."
  value       = module.iam.secret_access_key
  sensitive   = true
}
