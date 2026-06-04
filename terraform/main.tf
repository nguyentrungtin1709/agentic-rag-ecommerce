module "s3" {
  source = "./modules/s3"

  project_name         = var.project_name
  environment          = var.environment
  use_terraform        = var.use_terraform
  aws_s3_force_destroy = var.aws_s3_force_destroy
}

module "iam" {
  source = "./modules/iam"

  project_name       = var.project_name
  environment        = var.environment
  use_terraform      = var.use_terraform
  aws_iam_user_path  = var.aws_iam_user_path
  aws_s3_bucket_arn  = module.s3.bucket_arn
  aws_s3_bucket_name = module.s3.bucket_name
}
