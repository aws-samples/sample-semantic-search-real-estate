#!/usr/bin/env python3
"""Run the private one-off ECS data-loader task and report its exit status."""

from __future__ import annotations

import argparse

import boto3
from botocore.exceptions import ClientError, WaiterError


APPLICATION_CONTAINER_NAME = "DataLoaderContainer"


def stack_outputs(client, stack_name: str) -> dict[str, str]:
    response = client.describe_stacks(StackName=stack_name)
    return {
        item["OutputKey"]: item["OutputValue"]
        for item in response["Stacks"][0].get("Outputs", [])
    }


def application_container(task: dict) -> dict:
    """Return the one named loader container, ignoring injected sidecars."""
    matches = [
        container
        for container in task.get("containers", [])
        if container.get("name") == APPLICATION_CONTAINER_NAME
    ]
    if len(matches) != 1:
        raise RuntimeError(
            f"expected exactly one {APPLICATION_CONTAINER_NAME}, found {len(matches)}"
        )
    return matches[0]


def loader_exit_status(task: dict) -> tuple[int, str]:
    """Read status only from the named application container."""
    container = application_container(task)
    exit_code = container.get("exitCode")
    if exit_code is None:
        return 1, container.get("reason") or "application exit code is unavailable"
    reason = container.get("reason") or task.get("stoppedReason") or "unknown"
    return exit_code, reason


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--region", required=True)
    parser.add_argument("--stack-name", default="RealEstateECSFargateStack")
    args = parser.parse_args()

    cloudformation = boto3.client("cloudformation", region_name=args.region)
    ecs = boto3.client("ecs", region_name=args.region)
    try:
        outputs = stack_outputs(cloudformation, args.stack_name)
        required = {
            "DataLoaderClusterName",
            "DataLoaderTaskDefinitionArn",
            "DataLoaderSubnetIds",
            "DataLoaderSecurityGroupId",
        }
        missing = sorted(required - outputs.keys())
        if missing:
            raise RuntimeError(f"missing stack outputs: {', '.join(missing)}")
        response = ecs.run_task(
            cluster=outputs["DataLoaderClusterName"],
            taskDefinition=outputs["DataLoaderTaskDefinitionArn"],
            launchType="FARGATE",
            count=1,
            networkConfiguration={
                "awsvpcConfiguration": {
                    "subnets": outputs["DataLoaderSubnetIds"].split(","),
                    "securityGroups": [outputs["DataLoaderSecurityGroupId"]],
                    "assignPublicIp": "DISABLED",
                }
            },
        )
        if response.get("failures"):
            raise RuntimeError(f"ECS refused the loader task: {response['failures']}")
        task_arn = response["tasks"][0]["taskArn"]
        print(f"Started data loader task {task_arn.rsplit('/', 1)[-1]}")
        ecs.get_waiter("tasks_stopped").wait(
            cluster=outputs["DataLoaderClusterName"],
            tasks=[task_arn],
            WaiterConfig={"Delay": 15, "MaxAttempts": 120},
        )
        task = ecs.describe_tasks(
            cluster=outputs["DataLoaderClusterName"], tasks=[task_arn]
        )["tasks"][0]
        exit_code, reason = loader_exit_status(task)
        print(
            f"{APPLICATION_CONTAINER_NAME} stopped with exit code {exit_code}"
        )
        if exit_code != 0:
            raise RuntimeError(f"data loader failed: {reason}")
    except (ClientError, WaiterError, RuntimeError) as error:
        raise SystemExit(f"ERROR: {error}") from error


if __name__ == "__main__":
    main()
