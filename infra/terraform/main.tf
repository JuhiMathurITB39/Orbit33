# AWS reference deployment (ECS Fargate + S3 + Secrets Manager). AUTHORED ONLY: never planned or applied (no terraform
# binary or AWS credentials in the build environment). Treat as a starting point and review before use.
terraform {
  required_version = ">= 1.6"
  required_providers { aws = { source = "hashicorp/aws", version = "~> 5.0" } }
}
variable "region"    { default = "us-east-1" }
variable "image"     { description = "ECR image URI for the ORBIT API" }
variable "subnet_ids" { type = list(string) }
variable "vpc_id"    { type = string }
provider "aws" { region = var.region }

resource "aws_s3_bucket" "lake" { bucket_prefix = "orbit-lake-" }
resource "aws_s3_bucket_public_access_block" "lake" {
  bucket = aws_s3_bucket.lake.id
  block_public_acls = true  block_public_policy = true  ignore_public_acls = true  restrict_public_buckets = true
}
resource "aws_s3_bucket_server_side_encryption_configuration" "lake" {
  bucket = aws_s3_bucket.lake.id
  rule { apply_server_side_encryption_by_default { sse_algorithm = "aws:kms" } }
}
resource "aws_secretsmanager_secret" "api_key" { name_prefix = "orbit-api-key-" }
resource "aws_ecs_cluster" "orbit" { name = "orbit" }
resource "aws_cloudwatch_log_group" "api" { name = "/orbit/api"  retention_in_days = 30 }
resource "aws_security_group" "api" {
  vpc_id = var.vpc_id
  ingress { from_port = 8000  to_port = 8000  protocol = "tcp"  cidr_blocks = ["10.0.0.0/8"] }  # internal only; front with an ALB + WAF
  egress  { from_port = 0  to_port = 0  protocol = "-1"  cidr_blocks = ["0.0.0.0/0"] }
}
resource "aws_iam_role" "task" {
  name_prefix = "orbit-task-"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Principal = { Service = "ecs-tasks.amazonaws.com" }, Action = "sts:AssumeRole" }] })
}
resource "aws_ecs_task_definition" "api" {
  family = "orbit-api"  requires_compatibilities = ["FARGATE"]  network_mode = "awsvpc"  cpu = 512  memory = 1024
  execution_role_arn = aws_iam_role.task.arn  task_role_arn = aws_iam_role.task.arn
  container_definitions = jsonencode([{ name = "api", image = var.image, essential = true,
    portMappings = [{ containerPort = 8000 }], environment = [{ name = "ORBIT_HOST", value = "0.0.0.0" }],
    secrets = [{ name = "ORBIT_API_KEY", valueFrom = aws_secretsmanager_secret.api_key.arn }],
    logConfiguration = { logDriver = "awslogs", options = { awslogs-group = aws_cloudwatch_log_group.api.name, awslogs-region = var.region, awslogs-stream-prefix = "api" } } }])
}
resource "aws_ecs_service" "api" {
  name = "orbit-api"  cluster = aws_ecs_cluster.orbit.id  task_definition = aws_ecs_task_definition.api.arn  desired_count = 1  launch_type = "FARGATE"
  network_configuration { subnets = var.subnet_ids  security_groups = [aws_security_group.api.id] }
}
