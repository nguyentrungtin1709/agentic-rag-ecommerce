# Name of the S3 bucket where Terraform state files are stored.
# Replace <your-terraform-state-bucket> with your actual S3 bucket name.
bucket         = "<your-terraform-state-bucket>"

# Path to the Terraform state file within the S3 bucket.
key            = "global/s3/terraform.tfstate"

# AWS region where the S3 bucket and DynamoDB table exist.
# Replace <your-region> with your AWS region, e.g., "ap-southeast-1".
region         = "<your-region>"

# Name of the DynamoDB table used for state locking.
# Replace <your-dynamodb_table> with your actual DynamoDB table name.
dynamodb_table = "<your-dynamodb_table>"