# Configuration for Terraform backend using S3 and DynamoDB
# S3 for state storage and DynamoDB for state locking
#
# To initialize this backend, run the following command with a backend configuration file:
# terraform init --backend-config="environments/backend-development.tfvars"
#
# The backend.tfvars file should contain your specific configuration.
# An example file 'backend.example.tfvars' is provided.
terraform {
  backend "s3" {}
}