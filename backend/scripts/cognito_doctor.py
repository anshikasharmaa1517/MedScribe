"""Create (or reuse) a doctor login in the Cognito doctor pool and print an ID token.

Run from backend/ with AWS credentials for the account:
    python -m scripts.cognito_doctor --pool <DoctorUserPoolId> --client <DoctorUserPoolClientId> \
        --email doctor@example.com --password 'Passw0rd!x' --doctor-id doc-demo-meera

The token goes into frontend/doctor/.env as VITE_ID_TOKEN (valid 1 hour).
custom:doctorId is what the API uses to find the DOC#<doctorId> profile.
"""
import argparse
import sys

import boto3
from botocore.exceptions import ClientError

from core.settings import AWS_REGION


def ensure_user(idp, pool, email, password, doctor_id):
    attrs = [
        {"Name": "email", "Value": email},
        {"Name": "email_verified", "Value": "true"},
        {"Name": "custom:doctorId", "Value": doctor_id},
    ]
    try:
        idp.admin_create_user(
            UserPoolId=pool, Username=email, UserAttributes=attrs, MessageAction="SUPPRESS"
        )
        print(f"created {email}")
    except ClientError as e:
        if e.response["Error"]["Code"] != "UsernameExistsException":
            raise
        idp.admin_update_user_attributes(UserPoolId=pool, Username=email, UserAttributes=attrs)
        print(f"reusing {email}")
    idp.admin_set_user_password(UserPoolId=pool, Username=email, Password=password, Permanent=True)


def id_token(idp, pool, client, email, password) -> str:
    out = idp.admin_initiate_auth(
        UserPoolId=pool, ClientId=client, AuthFlow="ADMIN_USER_PASSWORD_AUTH",
        AuthParameters={"USERNAME": email, "PASSWORD": password},
    )
    return out["AuthenticationResult"]["IdToken"]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", required=True)
    ap.add_argument("--client", required=True)
    ap.add_argument("--email", required=True)
    ap.add_argument("--password", required=True)
    ap.add_argument("--doctor-id", default="doc-demo-meera")
    ap.add_argument("--reviewer", action="store_true",
                    help="also add to the 'reviewers' group (Tier 2 review screen)")
    ap.add_argument("--region", default=AWS_REGION)
    args = ap.parse_args(argv)

    idp = boto3.client("cognito-idp", region_name=args.region)
    ensure_user(idp, args.pool, args.email, args.password, args.doctor_id)
    if args.reviewer:
        try:
            idp.create_group(GroupName="reviewers", UserPoolId=args.pool,
                             Description="May approve/reject Tier 2 proposals")
        except ClientError as e:
            if e.response["Error"]["Code"] != "GroupExistsException":
                raise
        idp.admin_add_user_to_group(UserPoolId=args.pool, Username=args.email,
                                    GroupName="reviewers")
        print("in group: reviewers")
    print("\nVITE_ID_TOKEN=" + id_token(idp, args.pool, args.client, args.email, args.password))
    return 0


if __name__ == "__main__":
    sys.exit(main())
