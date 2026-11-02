import aws_cdk as cdk
from aws_cdk import (
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
    aws_cognito as cognito,
    aws_ec2 as ec2,
    aws_iam as iam,
    aws_location as location,
    aws_logs as logs,
    aws_opensearchservice as opensearch,
    aws_secretsmanager as secretsmanager,
)
from constructs import Construct


class RealEstateSearchStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.vpc = ec2.Vpc(
            self,
            "RealEstateVpc",
            max_azs=2,
            nat_gateways=1,
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="Public",
                    subnet_type=ec2.SubnetType.PUBLIC,
                    cidr_mask=24,
                ),
                ec2.SubnetConfiguration(
                    name="Application",
                    subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS,
                    cidr_mask=24,
                ),
                ec2.SubnetConfiguration(
                    name="Data",
                    subnet_type=ec2.SubnetType.PRIVATE_ISOLATED,
                    cidr_mask=24,
                ),
            ],
        )
        self.vpc.add_gateway_endpoint(
            "S3GatewayEndpoint",
            service=ec2.GatewayVpcEndpointAwsService.S3,
        )

        self.user_pool = cognito.UserPool(
            self,
            "RealEstateUserPool",
            user_pool_name="real-estate-search-users",
            sign_in_aliases=cognito.SignInAliases(username=True, email=True),
            auto_verify=cognito.AutoVerifiedAttrs(email=True),
            password_policy=cognito.PasswordPolicy(
                min_length=12,
                require_lowercase=True,
                require_uppercase=True,
                require_digits=True,
                require_symbols=True,
            ),
            self_sign_up_enabled=False,
            removal_policy=RemovalPolicy.DESTROY,
        )
        self.user_pool_client = cognito.UserPoolClient(
            self,
            "RealEstateUserPoolClient",
            user_pool=self.user_pool,
            user_pool_client_name="real-estate-search-client",
            # frontend/app.py signs in with USER_PASSWORD_AUTH. If sign-in moves
            # to SRP, also remove user_password=True here.
            auth_flows=cognito.AuthFlow(user_password=True, user_srp=True),
            # The app does not use the hosted UI. Disabling OAuth removes the CDK
            # default flows, scopes, and placeholder callback URL, so adding a
            # user pool domain later does not expose them.
            disable_o_auth=True,
            generate_secret=False,
            prevent_user_existence_errors=True,
            access_token_validity=Duration.hours(1),
            id_token_validity=Duration.hours(1),
            refresh_token_validity=Duration.days(1),
        )
        self.identity_pool = cognito.CfnIdentityPool(
            self,
            "RealEstateIdentityPool",
            identity_pool_name="real-estate-search-identity",
            allow_unauthenticated_identities=False,
            cognito_identity_providers=[
                cognito.CfnIdentityPool.CognitoIdentityProviderProperty(
                    client_id=self.user_pool_client.user_pool_client_id,
                    provider_name=self.user_pool.user_pool_provider_name,
                )
            ],
        )

        self.authenticated_role = iam.Role(
            self,
            "CognitoAuthenticatedRole",
            assumed_by=iam.FederatedPrincipal(
                "cognito-identity.amazonaws.com",
                conditions={
                    "StringEquals": {
                        "cognito-identity.amazonaws.com:aud": self.identity_pool.ref
                    },
                    "ForAnyValue:StringLike": {
                        "cognito-identity.amazonaws.com:amr": "authenticated"
                    },
                },
                assume_role_action="sts:AssumeRoleWithWebIdentity",
            ),
            description="Role for authenticated real-estate sample users",
        )
        self.loader_task_role = iam.Role(
            self,
            "DataLoaderTaskRole",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
            description="Role for the one-off synthetic data loader task",
        )

        domain_arn = (
            f"arn:{cdk.Aws.PARTITION}:es:{self.region}:{self.account}:"
            "domain/real-estate-search"
        )
        authenticated_search_actions = ["es:ESHttpPost"]
        authenticated_search_resources = [
            f"{domain_arn}/real-estate-properties*/_search"
        ]
        loader_resources = [
            domain_arn,
            f"{domain_arn}/real-estate-properties*",
            f"{domain_arn}/real-estate-properties*/*",
        ]
        loader_actions = [
            "es:ESHttpGet",
            "es:ESHttpPost",
            "es:ESHttpHead",
            "es:ESHttpPut",
            "es:ESHttpDelete",
        ]
        self.authenticated_role.add_to_policy(
            iam.PolicyStatement(
                actions=authenticated_search_actions,
                resources=authenticated_search_resources,
            )
        )
        self.loader_task_role.add_to_policy(
            iam.PolicyStatement(actions=loader_actions, resources=loader_resources)
        )

        titan_arn = (
            f"arn:{cdk.Aws.PARTITION}:bedrock:{self.region}::"
            "foundation-model/amazon.titan-embed-text-v2:0"
        )
        inference_profile_arn = (
            f"arn:{cdk.Aws.PARTITION}:bedrock:{self.region}:{self.account}:"
            "inference-profile/us.amazon.nova-2-lite-v1:0"
        )
        nova_model_arn = (
            f"arn:{cdk.Aws.PARTITION}:bedrock:*::"
            "foundation-model/amazon.nova-2-lite-v1:0"
        )
        self.authenticated_role.add_to_policy(
            iam.PolicyStatement(
                actions=["bedrock:InvokeModel"],
                resources=[titan_arn, inference_profile_arn],
            )
        )
        self.authenticated_role.add_to_policy(
            iam.PolicyStatement(
                actions=["bedrock:InvokeModel"],
                resources=[nova_model_arn],
                conditions={
                    "StringEquals": {
                        "bedrock:InferenceProfileArn": inference_profile_arn,
                    }
                },
            )
        )
        self.loader_task_role.add_to_policy(
            iam.PolicyStatement(actions=["bedrock:InvokeModel"], resources=[titan_arn])
        )

        self.opensearch_security_group = ec2.SecurityGroup(
            self,
            "OpenSearchSecurityGroup",
            vpc=self.vpc,
            description="Allows HTTPS only from sample application task groups",
            allow_all_outbound=True,
        )
        application_log_group = logs.LogGroup(
            self,
            "OpenSearchApplicationLogs",
            retention=logs.RetentionDays.ONE_WEEK,
            removal_policy=RemovalPolicy.DESTROY,
        )
        slow_search_log_group = logs.LogGroup(
            self,
            "OpenSearchSlowSearchLogs",
            retention=logs.RetentionDays.ONE_WEEK,
            removal_policy=RemovalPolicy.DESTROY,
        )
        slow_index_log_group = logs.LogGroup(
            self,
            "OpenSearchSlowIndexLogs",
            retention=logs.RetentionDays.ONE_WEEK,
            removal_policy=RemovalPolicy.DESTROY,
        )
        self.opensearch_domain = opensearch.Domain(
            self,
            "RealEstateOpenSearchDomain",
            domain_name="real-estate-search",
            version=opensearch.EngineVersion.OPENSEARCH_3_5,
            capacity=opensearch.CapacityConfig(
                data_nodes=1,
                data_node_instance_type="m6g.large.search",
                multi_az_with_standby_enabled=False,
            ),
            ebs=opensearch.EbsOptions(volume_size=20, volume_type=ec2.EbsDeviceVolumeType.GP3),
            zone_awareness=opensearch.ZoneAwarenessConfig(enabled=False),
            encryption_at_rest=opensearch.EncryptionAtRestOptions(enabled=True),
            node_to_node_encryption=True,
            enforce_https=True,
            tls_security_policy=opensearch.TLSSecurityPolicy.TLS_1_2,
            vpc=self.vpc,
            vpc_subnets=[
                ec2.SubnetSelection(subnets=[self.vpc.isolated_subnets[0]])
            ],
            security_groups=[self.opensearch_security_group],
            logging=opensearch.LoggingOptions(
                app_log_enabled=True,
                app_log_group=application_log_group,
                slow_search_log_enabled=True,
                slow_search_log_group=slow_search_log_group,
                slow_index_log_enabled=True,
                slow_index_log_group=slow_index_log_group,
            ),
            access_policies=[
                iam.PolicyStatement(
                    principals=[self.authenticated_role],
                    actions=authenticated_search_actions,
                    resources=authenticated_search_resources,
                ),
                iam.PolicyStatement(
                    principals=[self.loader_task_role],
                    actions=loader_actions,
                    resources=loader_resources,
                ),
            ],
            removal_policy=RemovalPolicy.DESTROY,
        )

        self.place_index = location.CfnPlaceIndex(
            self,
            "RealEstatePlaceIndex",
            index_name="real-estate-geocoding",
            data_source="Esri",
            description="Place index for the semantic-search sample",
        )
        self.authenticated_role.add_to_policy(
            iam.PolicyStatement(
                actions=["geo:SearchPlaceIndexForText"],
                resources=[self.place_index.attr_arn],
            )
        )
        cognito.CfnIdentityPoolRoleAttachment(
            self,
            "IdentityPoolRoleAttachment",
            identity_pool_id=self.identity_pool.ref,
            roles={"authenticated": self.authenticated_role.role_arn},
        )

        self.demo_password_secret = secretsmanager.Secret(
            self,
            "DemoPasswordSecret",
            secret_name="real-estate-semantic-search/demo-user",
            description="Operator-retrieved password for the optional sample user",
            generate_secret_string=secretsmanager.SecretStringGenerator(
                secret_string_template='{"username":"demo-user"}',
                generate_string_key="DEMO_PASSWORD",
                password_length=20,
            ),
            removal_policy=RemovalPolicy.DESTROY,
        )

        outputs = {
            "UserPoolId": self.user_pool.user_pool_id,
            "UserPoolClientId": self.user_pool_client.user_pool_client_id,
            "IdentityPoolId": self.identity_pool.ref,
            "OpenSearchDomainEndpoint": self.opensearch_domain.domain_endpoint,
            "OpenSearchDomainArn": self.opensearch_domain.domain_arn,
            "PlaceIndexName": self.place_index.index_name,
            "DemoPasswordSecretArn": self.demo_password_secret.secret_arn,
            "AWSRegion": self.region,
        }
        for output_id, value in outputs.items():
            CfnOutput(self, output_id, value=value)
