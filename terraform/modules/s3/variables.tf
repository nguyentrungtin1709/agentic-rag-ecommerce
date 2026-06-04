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

variable "aws_s3_force_destroy" {
  type        = bool
  description = "Allow bucket destruction even when non-empty. Should be true only for non-production environments."
}
