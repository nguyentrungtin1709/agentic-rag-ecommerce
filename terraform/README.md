# AWS Infrastructure

Terraform configuration for the `agentic-rag-ecommerce` project.

---

## Configure AWS Credentials

Before running any Terraform commands, ensure your AWS credentials are configured:

```bash
aws configure
# AWS Access Key ID:     <your-access-key-id>
# AWS Secret Access Key: <your-secret-access-key>
# Default region name:   ap-southeast-1
# Default output format: json
```

---

## Bootstrap Terraform Backend

Run these scripts **once** before the first `terraform init`. They create the
S3 bucket and DynamoDB table used to store remote state and prevent concurrent
apply conflicts.

### S3 Bucket for Terraform State

```bash
# Make scripts executable (run once)
chmod +x scripts/aws/*.sh
```

```bash
# Development (default)
bash scripts/aws/setup-state-bucket.sh development

# Other environments
bash scripts/aws/setup-state-bucket.sh staging
bash scripts/aws/setup-state-bucket.sh production
```

Creates: `agentic-rag-ecommerce-<environment>-terraform-state`

### DynamoDB Table for State Locking

```bash
# Development (default)
bash scripts/aws/setup-lock-table.sh development

# Other environments
bash scripts/aws/setup-lock-table.sh staging
bash scripts/aws/setup-lock-table.sh production
```

Creates: `agentic-rag-ecommerce-<environment>-terraform-lock-table`

---

## Deployment Steps

All commands below must be run from the `terraform/` directory.

### 1. Initialize Terraform

```bash
terraform init --backend-config="environments/backend-development.tfvars"
```

Switch environment by changing the backend config file:

```bash
terraform init --backend-config="environments/backend-production.tfvars" --reconfigure
```

### 2. Review the Plan

```bash
# Development
terraform plan --var-file="environments/development.tfvars" -out terraform.tfplan

# Production
terraform plan --var-file="environments/production.tfvars" -out terraform.tfplan
```

Optionally convert to JSON for review or audit:

```bash
terraform show -json terraform.tfplan > terraform.tfplan.json
```

### 3. Apply Changes

```bash
# Development
terraform apply --var-file="environments/development.tfvars"

# Production
terraform apply --var-file="environments/production.tfvars"
```

Enter `yes` when prompted for confirmation.

### 4. View Outputs

```bash
terraform output
```

### 5. Format and Validate

Run these before committing any Terraform changes:

```bash
terraform fmt -recursive
terraform validate
```

---

## Destroy Infrastructure

> **WARNING**: NEVER run `terraform destroy` against the production environment.
> Doing so will permanently delete live infrastructure, cause downtime, and may
> result in irreversible data loss. Only destroy non-production environments.

```bash
# Development only
terraform destroy --var-file="environments/development.tfvars"
```
