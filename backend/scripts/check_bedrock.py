"""Verify Bedrock model access with one trivial converse call.

Run from backend/:
    python -m scripts.check_bedrock                 # BEDROCK_MODEL_ID from env or ../.env
    python -m scripts.check_bedrock --list          # Anthropic models + cross-region profiles
    python -m scripts.check_bedrock --model <id>    # try a specific id, e.g. an apac. profile
"""
import argparse
import os
import sys
import time
from pathlib import Path


def _load_dotenv():
    env_file = Path(__file__).resolve().parent.parent.parent / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()

import boto3  # noqa: E402

from core.bedrock_client import BedrockAPIError, BedrockClient, BedrockResponseError  # noqa: E402
from core.settings import AWS_REGION, BEDROCK_MODEL_ID  # noqa: E402


def list_models(region):
    bedrock = boto3.client("bedrock", region_name=region)
    print(f"Anthropic foundation models in {region}:")
    try:
        for m in bedrock.list_foundation_models(byProvider="Anthropic")["modelSummaries"]:
            kinds = ",".join(m.get("inferenceTypesSupported", []))
            print(f"  {m['modelId']:60} {kinds}")
    except Exception as e:  # noqa: BLE001 - diagnostic script, show whatever failed
        print(f"  (could not list: {e})")

    print(f"\nCross-region inference profiles visible from {region}:")
    try:
        profiles = bedrock.list_inference_profiles(typeEquals="SYSTEM_DEFINED")
        for p in profiles["inferenceProfileSummaries"]:
            if "anthropic" in p["inferenceProfileId"]:
                print(f"  {p['inferenceProfileId']}")
    except Exception as e:  # noqa: BLE001
        print(f"  (could not list: {e})")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--list", action="store_true", help="list visible models and exit")
    parser.add_argument("--model", help="override BEDROCK_MODEL_ID for this run")
    parser.add_argument("--region", default=AWS_REGION)
    args = parser.parse_args()

    if args.list:
        list_models(args.region)
        return 0

    model_id = args.model or BEDROCK_MODEL_ID
    if not model_id:
        print("BEDROCK_MODEL_ID is not set. Set it in .env or pass --model.\n")
        list_models(args.region)
        return 1

    print(f"region: {args.region}\nmodel:  {model_id}\n")
    client = BedrockClient(model_id=model_id, region=args.region, max_tokens=100)

    started = time.perf_counter()
    try:
        result = client.converse_json(
            system="Reply with a single JSON object and nothing else.",
            user='Return exactly: {"ok": true, "model": "<the name you identify as>"}',
        )
    except BedrockAPIError as e:
        print(f"API ERROR: {e}")
        if "AccessDenied" in str(e) or "ValidationException" in str(e):
            print(
                "\nHint: the model may not be enabled or not offered on-demand in this region."
                "\nRun with --list and try a cross-region profile id (apac. prefix)."
            )
        return 1
    except BedrockResponseError as e:
        print(f"RESPONSE ERROR: {e}")
        return 1

    elapsed_ms = (time.perf_counter() - started) * 1000
    print(f"OK ({elapsed_ms:.0f} ms)\n{result}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
