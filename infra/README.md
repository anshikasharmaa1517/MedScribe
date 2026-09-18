# infra

Not built yet. Planned: AWS SAM, everything in `ap-south-1` except the Alexa
Lambda (`us-east-1`, an Alexa Skills Kit constraint).

- `template.yaml` — API Gateway (HTTP + WebSocket), Lambdas, DynamoDB single table
  `medscribe`, S3 bucket, Cognito user pools (doctors, patients).
- `statemachine/approval.asl.json` — the post-approval Step Functions pipeline:
  FinalTranscript → FinalExtract → ResolveValidate → gate on unresolved items →
  RenderPDF → PersistRx → SendPrescription → SendGenerics → ScheduleReminders.
- `layers/` — WeasyPrint and Python dependency layers.

Secrets (Sarvam, Twilio) go in Secrets Manager or SSM, never in the repo — it may
need to be public for judging.
