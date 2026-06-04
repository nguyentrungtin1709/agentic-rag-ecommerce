locals {
  user_name   = "${var.project_name}-${var.environment}-s3-user"
  group_name  = "${var.project_name}-${var.environment}-s3-group"
  policy_name = "${var.project_name}-${var.environment}-s3-policy"

  tags = {
    Project     = var.project_name
    Environment = var.environment
    Terraform   = tostring(var.use_terraform)
  }
}

# ── IAM User ──────────────────────────────────────────────────────────────────

resource "aws_iam_user" "s3" {
  name = local.user_name
  path = var.aws_iam_user_path

  tags = merge(local.tags, {
    Name = local.user_name
  })
}

# ── IAM Group ─────────────────────────────────────────────────────────────────

resource "aws_iam_group" "s3" {
  name = local.group_name
  path = "/"
}

resource "aws_iam_user_group_membership" "s3" {
  user   = aws_iam_user.s3.name
  groups = [aws_iam_group.s3.name]
}

# ── IAM Policy ────────────────────────────────────────────────────────────────

data "aws_iam_policy_document" "s3_full_access" {
  statement {
    sid    = "AllowBucketLevelActions"
    effect = "Allow"

    actions = [
      "s3:ListBucket",
      "s3:GetBucketLocation",
    ]

    resources = [var.aws_s3_bucket_arn]
  }

  statement {
    sid    = "AllowObjectLevelActions"
    effect = "Allow"

    actions = [
      "s3:PutObject",
      "s3:GetObject",
      "s3:DeleteObject",
      "s3:GetObjectAcl",
      "s3:PutObjectAcl",
    ]

    resources = ["${var.aws_s3_bucket_arn}/*"]
  }
}

resource "aws_iam_policy" "s3_full_access" {
  name        = local.policy_name
  description = "Allows full object management on the ${var.aws_s3_bucket_name} bucket."
  policy      = data.aws_iam_policy_document.s3_full_access.json

  tags = merge(local.tags, {
    Name = local.policy_name
  })
}

resource "aws_iam_group_policy_attachment" "s3" {
  group      = aws_iam_group.s3.name
  policy_arn = aws_iam_policy.s3_full_access.arn
}

# ── Access Key ────────────────────────────────────────────────────────────────

resource "aws_iam_access_key" "s3" {
  user = aws_iam_user.s3.name
}

# ── SSM Parameters ────────────────────────────────────────────────────────────
# Credentials are stored as SecureString to keep them out of plain-text outputs.
# Retrieve with: aws ssm get-parameter --name "<path>" --with-decryption

resource "aws_ssm_parameter" "access_key_id" {
  name        = "/${var.project_name}/${var.environment}/api/iam/AWS_ACCESS_KEY_ID"
  type        = "SecureString"
  value       = aws_iam_access_key.s3.id
  description = "IAM access key ID for the ${local.user_name} user."

  tags = merge(local.tags, {
    Name = "${var.project_name}-${var.environment}-ssm-access-key-id"
  })
}

resource "aws_ssm_parameter" "secret_access_key" {
  name        = "/${var.project_name}/${var.environment}/api/iam/AWS_SECRET_ACCESS_KEY"
  type        = "SecureString"
  value       = aws_iam_access_key.s3.secret
  description = "IAM secret access key for the ${local.user_name} user."

  tags = merge(local.tags, {
    Name = "${var.project_name}-${var.environment}-ssm-secret-access-key"
  })
}
