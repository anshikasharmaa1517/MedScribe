"""Create (or reuse) a patient login in the Cognito patient pool. Username = phone (E.164).

Run from backend/ with AWS credentials:
    python -m scripts.cognito_patient --pool <PatientUserPoolId> \
        --client <PatientUserPoolClientId> \
        --phone +919800000001 --password 'Patient#2026'

The phone must match a PAT#/PHONE# record (seed_demo_data creates three), because the
API resolves the patient from the token's phone_number claim.
"""
import argparse
import sys

import boto3
from botocore.exceptions import ClientError

from core.settings import AWS_REGION


def ensure_user(idp, pool, phone, password):
    attrs = [{"Name": "phone_number", "Value": phone},
             {"Name": "phone_number_verified", "Value": "true"}]
    try:
        idp.admin_create_user(UserPoolId=pool, Username=phone, UserAttributes=attrs,
                              MessageAction="SUPPRESS")
        print(f"created {phone}")
    except ClientError as e:
        if e.response["Error"]["Code"] != "UsernameExistsException":
            raise
        print(f"reusing {phone}")
    idp.admin_set_user_password(UserPoolId=pool, Username=phone, Password=password, Permanent=True)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", required=True)
    ap.add_argument("--client", required=True)
    ap.add_argument("--phone", required=True)
    ap.add_argument("--password", required=True)
    ap.add_argument("--region", default=AWS_REGION)
    args = ap.parse_args(argv)
    idp = boto3.client("cognito-idp", region_name=args.region)
    ensure_user(idp, args.pool, args.phone, args.password)
    out = idp.admin_initiate_auth(UserPoolId=args.pool, ClientId=args.client,
                                  AuthFlow="ADMIN_USER_PASSWORD_AUTH",
                                  AuthParameters={"USERNAME": args.phone,
                                                  "PASSWORD": args.password})
    print("\nID token (1 h):", out["AuthenticationResult"]["IdToken"][:40] + "...")
    return 0


if __name__ == "__main__":
    sys.exit(main())
