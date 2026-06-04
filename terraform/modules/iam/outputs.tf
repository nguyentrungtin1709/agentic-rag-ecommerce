output "user_name" {
  description = "Name of the IAM user."
  value       = aws_iam_user.s3.name
}

output "user_arn" {
  description = "ARN of the IAM user."
  value       = aws_iam_user.s3.arn
}

output "group_name" {
  description = "Name of the IAM group."
  value       = aws_iam_group.s3.name
}

output "policy_arn" {
  description = "ARN of the S3 access policy."
  value       = aws_iam_policy.s3_full_access.arn
}

output "access_key_id" {
  description = "IAM access key ID. Also stored in SSM at /<project>/<env>/api/iam/AWS_ACCESS_KEY_ID."
  value       = aws_iam_access_key.s3.id
  sensitive   = true
}

output "secret_access_key" {
  description = "IAM secret access key. Also stored in SSM at /<project>/<env>/api/iam/AWS_SECRET_ACCESS_KEY."
  value       = aws_iam_access_key.s3.secret
  sensitive   = true
}
