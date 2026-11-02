#!/usr/bin/env python3
"""Create or reset a Cognito demo user without displaying its password."""

from __future__ import annotations

import argparse
import json
import secrets
import string
import sys

import boto3
from botocore.exceptions import ClientError


def get_stack_outputs(stack_name: str, region: str) -> dict[str, str]:
    client = boto3.client("cloudformation", region_name=region)
    response = client.describe_stacks(StackName=stack_name)
    if not response.get("Stacks"):
        raise RuntimeError(f"stack {stack_name} was not found")
    return {
        output["OutputKey"]: output["OutputValue"]
        for output in response["Stacks"][0].get("Outputs", [])
    }


def generate_secure_password() -> str:
    """Generate a 20-character password with every required character class."""
    alphabet = string.ascii_letters + string.digits + "!@#%^&*-_=+"
    characters = [
        secrets.choice(string.ascii_uppercase),
        secrets.choice(string.ascii_lowercase),
        secrets.choice(string.digits),
        secrets.choice("!@#%^&*-_=+"),
    ]
    characters.extend(secrets.choice(alphabet) for _ in range(16))
    secrets.SystemRandom().shuffle(characters)
    return "".join(characters)


def update_secret(secret_arn: str, username: str, password: str, region: str) -> None:
    client = boto3.client("secretsmanager", region_name=region)
    try:
        response = client.get_secret_value(SecretId=secret_arn)
        current = json.loads(response["SecretString"])
        if not isinstance(current, dict):
            current = {}
    except client.exceptions.ResourceNotFoundException:
        current = {}
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        raise RuntimeError("the demo password secret is not valid JSON") from error
    current.update({"username": username, "DEMO_PASSWORD": password})
    client.update_secret(SecretId=secret_arn, SecretString=json.dumps(current))


def create_demo_user(
    user_pool_id: str,
    region: str,
    username: str,
    email: str,
    secret_arn: str,
) -> dict[str, str] | None:
    client = boto3.client("cognito-idp", region_name=region)
    password = generate_secure_password()
    try:
        try:
            client.admin_get_user(UserPoolId=user_pool_id, Username=username)
            client.admin_set_user_password(
                UserPoolId=user_pool_id,
                Username=username,
                Password=password,
                Permanent=True,
            )
            operation = "reset"
        except client.exceptions.UserNotFoundException:
            client.admin_create_user(
                UserPoolId=user_pool_id,
                Username=username,
                UserAttributes=[
                    {"Name": "email", "Value": email},
                    {"Name": "email_verified", "Value": "true"},
                ],
                TemporaryPassword=password,
                MessageAction="SUPPRESS",
                ForceAliasCreation=False,
            )
            client.admin_set_user_password(
                UserPoolId=user_pool_id,
                Username=username,
                Password=password,
                Permanent=True,
            )
            operation = "created"

        update_secret(secret_arn, username, password, region)
        print(f"Demo user {operation}: {username}")
        print(f"Password secret: {secret_arn}")
        print(
            "Retrieve it when needed with: aws secretsmanager get-secret-value "
            f"--secret-id '{secret_arn}' --region '{region}' "
            "--query SecretString --output text"
        )
        return {"username": username, "secret_arn": secret_arn}
    except (ClientError, RuntimeError) as error:
        print(f"ERROR: demo user setup failed: {error}", file=sys.stderr)
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stack-name", default="RealEstateSearchStack")
    parser.add_argument("--region", required=True)
    parser.add_argument("--username", default="demo-user")
    parser.add_argument("--email", default="demo@example.com")
    args = parser.parse_args()

    try:
        outputs = get_stack_outputs(args.stack_name, args.region)
        user_pool_id = outputs["UserPoolId"]
        secret_arn = outputs["DemoPasswordSecretArn"]
    except (ClientError, KeyError, RuntimeError) as error:
        raise SystemExit(f"ERROR: cannot read required stack outputs: {error}") from error

    result = create_demo_user(
        user_pool_id,
        args.region,
        args.username,
        args.email,
        secret_arn,
    )
    if result is None:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
