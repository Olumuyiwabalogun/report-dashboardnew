locals {
  name = "report-dashboard-${var.env}"
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

# Report Studio owns these. The dashboard only reads them.
data "aws_s3_bucket" "reports" {
  bucket = var.reports_bucket_name
}

data "aws_dynamodb_table" "reports" {
  name = var.table_name
}

# ---------- Private bucket for the register (children's data) ----------
resource "aws_s3_bucket" "register" {
  bucket = "${local.name}-register-${data.aws_caller_identity.current.account_id}"
}

resource "aws_s3_bucket_public_access_block" "register" {
  bucket                  = aws_s3_bucket.register.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "register" {
  bucket = aws_s3_bucket.register.id
  versioning_configuration {
    status = "Enabled" # every upload keeps the previous version
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "register" {
  bucket = aws_s3_bucket.register.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "register" {
  bucket = aws_s3_bucket.register.id
  rule {
    id     = "expire-old-versions"
    status = "Enabled"
    filter {}
    noncurrent_version_expiration {
      noncurrent_days = 365
    }
  }
  depends_on = [aws_s3_bucket_versioning.register]
}

data "aws_iam_policy_document" "register_tls_only" {
  statement {
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.register.arn, "${aws_s3_bucket.register.arn}/*"]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "register" {
  bucket     = aws_s3_bucket.register.id
  policy     = data.aws_iam_policy_document.register_tls_only.json
  depends_on = [aws_s3_bucket_public_access_block.register]
}

# ---------- IAM: least privilege ----------
data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "web" {
  name               = "${local.name}-web"
  assume_role_policy = data.aws_iam_policy_document.assume.json
}

resource "aws_iam_role_policy_attachment" "web_logs" {
  role       = aws_iam_role.web.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "web" {
  statement {
    actions   = ["dynamodb:Scan", "dynamodb:GetItem"]
    resources = [data.aws_dynamodb_table.reports.arn]
  }
  statement {
    actions   = ["s3:GetObject"]
    resources = [
      "${data.aws_s3_bucket.reports.arn}/outputs/*", # PDFs and Excel downloads
      "${data.aws_s3_bucket.reports.arn}/data/*",    # saved rows, read for attendance counts
    ]
  }
  statement {
    actions   = ["ssm:GetParameter"]
    resources = [
      "arn:aws:ssm:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:parameter${var.password_param}",
      "arn:aws:ssm:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:parameter${var.admin_password_param}",
    ]
  }
  statement {
    actions   = ["s3:PutObject", "s3:GetObject"]
    resources = ["${aws_s3_bucket.register.arn}/register/*"]
  }
}

resource "aws_iam_role_policy" "web" {
  name   = "access"
  role   = aws_iam_role.web.id
  policy = data.aws_iam_policy_document.web.json
}

# ---------- Lambda ----------
# Terraform creates the function with a placeholder. The app pipeline ships the real code,
# so lifecycle ignores code changes. Infra and app deploy independently.
data "archive_file" "placeholder" {
  type        = "zip"
  output_path = "${path.module}/placeholder.zip"

  source {
    content  = "def handler(event, context):\n    return {'statusCode': 503, 'body': 'not deployed yet'}\n"
    filename = "lambda_handler.py"
  }
}

resource "aws_lambda_function" "web" {
  function_name    = "${local.name}-web"
  role             = aws_iam_role.web.arn
  runtime          = "python3.12"
  handler          = "lambda_handler.handler"
  memory_size      = 512
  timeout          = 15
  filename         = data.archive_file.placeholder.output_path
  source_code_hash = data.archive_file.placeholder.output_base64sha256

  environment {
    variables = {
      TABLE_NAME           = var.table_name
      BUCKET_NAME          = var.reports_bucket_name
      DASH_PASSWORD_PARAM  = var.password_param
      EXCLUDE_IDS          = var.exclude_ids
      ADMIN_PASSWORD_PARAM = var.admin_password_param
      REGISTER_BUCKET      = aws_s3_bucket.register.id
    }
  }

  lifecycle {
    ignore_changes = [filename, source_code_hash]
  }
}

# ---------- HTTP API -> Lambda ----------
resource "aws_apigatewayv2_api" "http" {
  name          = local.name
  protocol_type = "HTTP"
}

resource "aws_apigatewayv2_integration" "web" {
  api_id                 = aws_apigatewayv2_api.http.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.web.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "default" {
  api_id    = aws_apigatewayv2_api.http.id
  route_key = "$default"
  target    = "integrations/${aws_apigatewayv2_integration.web.id}"
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.http.id
  name        = "$default"
  auto_deploy = true
}

resource "aws_lambda_permission" "api_invoke" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.web.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.http.execution_arn}/*/*"
}
