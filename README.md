# Report Dashboard

Flask + Chart.js dashboard over the reports Report Studio already records.

    Report Studio table (DynamoDB) -> dashboard Lambda (via API Gateway) -> browser
    PDF / Excel downloads: presigned S3 links (5 minutes), only after sign-in

## Run locally (no AWS needed)
    pip install -r requirements-dev.txt
    DEMO_MODE=1 flask --app app run --debug
    pytest && flake8 --max-line-length 100 .

## Environment variables
| Name | Purpose |
|---|---|
| TABLE_NAME | Report Studio's DynamoDB table (read-only) |
| BUCKET_NAME | Report Studio's bucket (only `outputs/*` is readable) |
| DASH_PASSWORD_PARAM | SSM SecureString holding the shared password. Unset = no login (local only) |
| ADMIN_PASSWORD_PARAM | SSM SecureString for the `/admin` register-upload page (separate password) |
| REGISTER_BUCKET | Private, versioned bucket holding `register/current.xlsx` |
| EXCLUDE_IDS | Device IDs that are not children (default `1`) |
| DEMO_MODE | `1` uses generated sample data |

## One-time per environment (two passwords: viewers and admins)
    aws ssm put-parameter --name /report-dashboard/dev/admin-password --type SecureString --value '<another password>'
    aws ssm put-parameter --name /report-dashboard/dev/password --type SecureString --value '<password>'

Handler: `lambda_handler.handler` (HTTP API, `$default` stage).
Dashboard role: `dynamodb:Scan/GetItem`, `s3:GetObject` on `outputs/*`, `ssm:GetParameter`.
