# infra

AWS SAM, everything in `ap-south-1` except the Alexa Lambda (`us-east-1`, an Alexa
Skills Kit constraint - not built yet).

- `template.yaml` - DynamoDB `medscribe-<env>` (PK/SK + GSI1 doctorId/SK) and the
  three reference tables, S3 bucket, Cognito user pools (doctors: email; patients:
  phone), HTTP API, `GET /health` Lambda.
- `samconfig.toml` - region pinned to `ap-south-1`, stack `medscribe-dev`.
- `statemachine/approval.asl.json` - (later) the post-approval Step Functions pipeline:
  FinalTranscript -> FinalExtract -> ResolveValidate -> gate on unresolved items ->
  RenderPDF -> PersistRx -> SendPrescription -> SendGenerics -> ScheduleReminders.
- `layers/` - (later) WeasyPrint and Python dependency layers. Build with
  `sam build --use-container` so rapidfuzz gets Linux/arm64 wheels, not Windows ones.

## Deploy

```
cd infra
sam build
sam deploy --guided        # first time; afterwards just `sam deploy`
```

The deploying IAM user needs CloudFormation, IAM role creation, Lambda, DynamoDB,
S3, Cognito, API Gateway and SSM. `AdministratorAccess` is the pragmatic hackathon
choice; the Lambda roles the template creates are least-privilege regardless.

Stack outputs give the API URL, table/bucket names and Cognito IDs.

## Secrets

Secrets live in SSM Parameter Store under `/medscribe/<env>/`, never in the
template or the repo (it may need to be public for judging). Create them once:

```
aws ssm put-parameter --name /medscribe/dev/SARVAM_API_KEY   --type SecureString --value ...
aws ssm put-parameter --name /medscribe/dev/TWILIO_AUTH_TOKEN --type SecureString --value ...
aws ssm put-parameter --name /medscribe/dev/BEDROCK_API_KEY   --type SecureString --value ...
```

`GET /health` lists which parameter names exist under the prefix (names only).

## Seeding

After deploy, from `backend/` with `MEDSCRIBE_TABLE` etc. set to the deployed names
(see stack outputs): `python -m scripts.seed_reference_tables` then
`python -m scripts.seed_demo_data`.

## Teardown (after results)

`sam delete` alone is not enough - the table and bucket are `Retain`ed so a bad deploy
can't destroy patient data. Full checklist, all in `ap-south-1` unless noted:

```
cd infra
sam delete --stack-name medscribe-dev                                # Lambda, API, Cognito, ref tables
aws s3 rm s3://medscribe-dev-<account-id> --recursive                # bucket must be empty first
aws s3 rb s3://medscribe-dev-<account-id>
aws dynamodb delete-table --table-name medscribe-dev                 # retained app table
aws logs delete-log-group --log-group-name /aws/lambda/medscribe-health-dev
aws ssm delete-parameters --names /medscribe/dev/SARVAM_API_KEY /medscribe/dev/TWILIO_AUTH_TOKEN /medscribe/dev/BEDROCK_API_KEY
aws cloudformation delete-stack --stack-name aws-sam-cli-managed-default   # SAM's own deploy bucket stack
```

Then in the console: Bedrock -> API keys (revoke the Mantle key), IAM -> user `anai`
(delete access keys, remove AdministratorAccess), and Twilio -> release the sandbox.
Check Billing -> Bills a day later to confirm nothing is still metering.
