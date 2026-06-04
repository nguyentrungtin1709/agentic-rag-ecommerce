locals {
  bucket_name = "${var.project_name}-${var.environment}-s3-media-bucket"

  tags = {
    Project     = var.project_name
    Environment = var.environment
    Terraform   = tostring(var.use_terraform)
  }
}

# ── S3 Bucket ─────────────────────────────────────────────────────────────────

resource "aws_s3_bucket" "media" {
  bucket        = local.bucket_name
  force_destroy = var.aws_s3_force_destroy

  tags = merge(local.tags, {
    Name = local.bucket_name
  })
}

# ── Versioning ────────────────────────────────────────────────────────────────

resource "aws_s3_bucket_versioning" "media" {
  bucket = aws_s3_bucket.media.id

  versioning_configuration {
    status = "Enabled"
  }
}

# ── Server-Side Encryption ────────────────────────────────────────────────────

resource "aws_s3_bucket_server_side_encryption_configuration" "media" {
  bucket = aws_s3_bucket.media.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# ── Ownership Controls ────────────────────────────────────────────────────────
# BucketOwnerEnforced disables ACLs so only bucket policies control access.

resource "aws_s3_bucket_ownership_controls" "media" {
  bucket = aws_s3_bucket.media.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

# ── Public Access Block ───────────────────────────────────────────────────────
# All four flags must be false to allow a public bucket policy.
# depends_on ensures ownership controls are set before the policy is applied.

resource "aws_s3_bucket_public_access_block" "media" {
  bucket = aws_s3_bucket.media.id

  block_public_acls       = false
  ignore_public_acls      = false
  block_public_policy     = false
  restrict_public_buckets = false

  depends_on = [aws_s3_bucket_ownership_controls.media]
}

# ── Public Read Policy ────────────────────────────────────────────────────────
# Grants anonymous s3:GetObject on all objects in the bucket.

data "aws_iam_policy_document" "public_read" {
  statement {
    sid    = "PublicReadGetObject"
    effect = "Allow"

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.media.arn}/*"]
  }
}

resource "aws_s3_bucket_policy" "public_read" {
  bucket = aws_s3_bucket.media.id
  policy = data.aws_iam_policy_document.public_read.json

  depends_on = [aws_s3_bucket_public_access_block.media]
}
