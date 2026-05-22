"""Probe what your AWS creds can do against Bedrock.

Reads BEDROCK_AWS_ACCESS_KEY_ID / BEDROCK_AWS_SECRET_ACCESS_KEY /
BEDROCK_AWS_REGION from the environment (falls back to AWS_* if those
are unset). Three checks:

  1. list_inference_profiles      — what application/system profiles exist
  2. list_foundation_models       — which base models the account knows about
  3. converse(arn)                — a 1-token smoke call against each ARN we
                                    care about so you can see which actually
                                    work end-to-end vs. which return AccessDenied

Run:  uv run python scripts/check_bedrock_access.py
"""

from __future__ import annotations

import os
import sys

import boto3
from botocore.exceptions import ClientError
from dotenv import load_dotenv

ARNS_TO_CHECK = [
    (
        "Custom Sonnet",
        "arn:aws:bedrock:us-west-2:823998119176:application-inference-profile/hnxtndg2c380",
    ),
    (
        "Custom Opus",
        "arn:aws:bedrock:us-west-2:823998119176:application-inference-profile/l4phmjq3xd8t",
    ),
    (
        "Custom Haiku",
        "arn:aws:bedrock:us-west-2:823998119176:application-inference-profile/ge5qern21zg5",
    ),
]


load_dotenv()


def _creds() -> dict[str, str]:
    region = (
        os.getenv("BEDROCK_AWS_REGION")
        or os.getenv("AWS_REGION_NAME")
        or os.getenv("AWS_REGION")
        or "us-west-2"
    )
    return {
        "region_name": region,
        "aws_access_key_id": os.getenv("BEDROCK_AWS_ACCESS_KEY_ID")
        or os.getenv("AWS_ACCESS_KEY_ID", ""),
        "aws_secret_access_key": os.getenv("BEDROCK_AWS_SECRET_ACCESS_KEY")
        or os.getenv("AWS_SECRET_ACCESS_KEY", ""),
    }


def list_inference_profiles() -> None:
    print("\n=== Inference profiles visible to these creds ===")
    client = boto3.client("bedrock", **_creds())
    try:
        for kind in ("APPLICATION", "SYSTEM_DEFINED"):
            try:
                resp = client.list_inference_profiles(typeEquals=kind)
            except ClientError as exc:
                print(f"[{kind}] list_inference_profiles failed: {exc.response['Error']['Code']}")
                continue
            profiles = resp.get("inferenceProfileSummaries", [])
            print(f"[{kind}] {len(profiles)} profile(s)")
            for profile in profiles:
                print(
                    f"  - {profile.get('inferenceProfileName')}"
                    f"  ({profile.get('inferenceProfileId')})"
                    f"  status={profile.get('status')}"
                )
                if profile.get("models"):
                    for model in profile["models"]:
                        print(f"      model: {model.get('modelArn')}")
    except ClientError as exc:
        print(f"list_inference_profiles failed: {exc}")


def list_foundation_models() -> None:
    print("\n=== Foundation models the account knows about ===")
    client = boto3.client("bedrock", **_creds())
    try:
        resp = client.list_foundation_models()
    except ClientError as exc:
        print(f"list_foundation_models failed: {exc}")
        return
    summaries = resp.get("modelSummaries", [])
    # Trim to text-capable models; the full list is long.
    text_models = [
        m
        for m in summaries
        if "TEXT" in (m.get("inputModalities") or [])
        and "TEXT" in (m.get("outputModalities") or [])
    ]
    print(f"{len(text_models)} text-in/text-out model(s) (showing modelId + provider):")
    for model in text_models:
        provider = model.get("providerName", "?")
        model_id = model.get("modelId", "?")
        on_demand = "ON_DEMAND" in (model.get("inferenceTypesSupported") or [])
        marker = "  on-demand" if on_demand else "  inference-profile-only"
        print(f"  [{provider:>12}] {model_id}{marker}")


def smoke_converse(label: str, model_or_arn: str) -> None:
    runtime = boto3.client("bedrock-runtime", **_creds())
    try:
        resp = runtime.converse(
            modelId=model_or_arn,
            messages=[{"role": "user", "content": [{"text": "Say 'ok' and nothing else."}]}],
            inferenceConfig={"maxTokens": 8, "temperature": 0.0},
        )
        text = resp["output"]["message"]["content"][0].get("text", "")
        print(f"  OK  {label:25s} → {text!r}")
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        print(f"  FAIL {label:24s} → {code}: {exc.response['Error']['Message']}")


def smoke_test_arns() -> None:
    print("\n=== Smoke test: 1-token converse() against each ARN ===")
    for label, arn in ARNS_TO_CHECK:
        smoke_converse(label, arn)


def main() -> int:
    creds = _creds()
    if not creds["aws_access_key_id"] or not creds["aws_secret_access_key"]:
        print("Missing BEDROCK_AWS_ACCESS_KEY_ID / BEDROCK_AWS_SECRET_ACCESS_KEY.")
        return 2
    print(f"Region: {creds['region_name']}")
    list_inference_profiles()
    list_foundation_models()
    smoke_test_arns()
    return 0


if __name__ == "__main__":
    sys.exit(main())
