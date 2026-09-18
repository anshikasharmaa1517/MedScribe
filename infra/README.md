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
