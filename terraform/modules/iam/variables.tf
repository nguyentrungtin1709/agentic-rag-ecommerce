variable "project_name" {
  type        = string
  description = "Project identifier used in resource names and tags."
}

variable "environment" {
  type        = string
  description = "Deployment environment: development, staging, or production."
}

variable "use_terraform" {
  type        = bool
  description = "Whether resources are managed by Terraform. Used in the Terraform tag."
}

variable "aws_iam_user_path" {
  type        = string
  description = "IAM path for the user (e.g. '/' for root path)."
}

variable "aws_s3_bucket_arn" {
  type        = string
  description = "ARN of the S3 media bucket. Used to scope the IAM policy."
}

variable "aws_s3_bucket_name" {
  type        = string
  description = "Name of the S3 media bucket. Used in SSM parameter descriptions."
}
