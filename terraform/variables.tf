# =============================================================================
# Global
# =============================================================================

variable "project_name" {
  type        = string
  description = "Project identifier used in all resource names and tags."
  default     = "agentic-rag-ecommerce"
}

variable "environment" {
  type        = string
  description = "Deployment environment: development, staging, or production."
  default     = "development"
}

variable "use_terraform" {
  type        = bool
  description = "Whether resources are managed by Terraform. Rendered as true/false in the Terraform tag."
  default     = true
}

variable "aws_region" {
  type        = string
  description = "AWS region where all resources will be created."
  default     = "ap-southeast-1"
}

# =============================================================================
# S3
# =============================================================================

variable "aws_s3_force_destroy" {
  type        = bool
  description = "Allow bucket destruction even when non-empty. Should be true only for non-production environments."
  default     = false
}

# =============================================================================
# IAM
# =============================================================================

variable "aws_iam_user_path" {
  type        = string
  description = "IAM path for the user. Defaults to root path."
  default     = "/"
}
