#!/usr/bin/env python3
import os
import aws_cdk as cdk
from real_estate_search_stack import RealEstateSearchStack
from ecs_fargate_stack import ECSFargateStack

app = cdk.App()

# Get environment variables or use defaults
account = os.environ.get('CDK_DEFAULT_ACCOUNT')
region = os.environ.get('CDK_DEFAULT_REGION')

if not region:
    print("❌ CDK_DEFAULT_REGION environment variable is required")
    print("   Set it with: export CDK_DEFAULT_REGION=your-region")
    exit(1)

env = cdk.Environment(account=account, region=region)

# Get deployment options from context
deploy_backend = app.node.try_get_context('deploy_backend') != 'false'
deploy_frontend = app.node.try_get_context('deploy_frontend') == 'true'
domain_name = app.node.try_get_context('domain_name')
cloudfront_certificate_arn = app.node.try_get_context(
    'cloudfront_certificate_arn'
)

if bool(domain_name) != bool(cloudfront_certificate_arn):
    raise ValueError(
        "domain_name and cloudfront_certificate_arn must be supplied together"
    )

# Always create the backend stack (required for frontend)
backend_stack = RealEstateSearchStack(
    app, 
    "RealEstateSearchStack",
    env=env,
    description="Real Estate Geo-Spatial RAG Search - Backend (OpenSearch, Cognito, Bedrock)"
)

# Create the frontend stack if requested
if deploy_frontend:
    frontend_stack = ECSFargateStack(
        app, 
        "RealEstateECSFargateStack",
        backend_stack=backend_stack,
        domain_name=domain_name,
        cloudfront_certificate_arn=cloudfront_certificate_arn,
        env=env,
        description="Real Estate Geo-Spatial RAG Search - Frontend (ECS Fargate)"
    )
    
    # Add dependency to ensure backend is deployed first
    frontend_stack.add_dependency(backend_stack)

app.synth()
