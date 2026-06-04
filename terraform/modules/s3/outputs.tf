output "bucket_name" {
  description = "Name of the S3 media bucket."
  value       = aws_s3_bucket.media.bucket
}

output "bucket_arn" {
  description = "ARN of the S3 media bucket."
  value       = aws_s3_bucket.media.arn
}

output "bucket_id" {
  description = "ID of the S3 media bucket (same as bucket name)."
  value       = aws_s3_bucket.media.id
}
