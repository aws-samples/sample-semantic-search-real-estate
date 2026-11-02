import aws_cdk as cdk
from pathlib import Path

from aws_cdk import (
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
    aws_certificatemanager as acm,
    aws_cloudfront as cloudfront,
    aws_cloudfront_origins as origins,
    aws_ec2 as ec2,
    aws_ecs as ecs,
    aws_elasticloadbalancingv2 as elbv2,
    aws_logs as logs,
    aws_s3 as s3,
    custom_resources,
)
from constructs import Construct


class ECSFargateStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        backend_stack,
        domain_name: str | None = None,
        cloudfront_certificate_arn: str | None = None,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        if bool(domain_name) != bool(cloudfront_certificate_arn):
            raise ValueError(
                "domain_name and cloudfront_certificate_arn must be supplied together"
            )

        solution_dir = Path(__file__).resolve().parents[2]
        certificate = None
        if cloudfront_certificate_arn:
            certificate = acm.Certificate.from_certificate_arn(
                self,
                "CloudFrontViewerCertificate",
                cloudfront_certificate_arn,
            )
        else:
            cdk.Annotations.of(self).add_warning(
                "The generated CloudFront hostname uses the provider-managed "
                "TLSv1 viewer policy and does not enforce TLS 1.2"
            )

        vpc = backend_stack.vpc
        cluster = ecs.Cluster(
            self,
            "RealEstateSemanticSearchCluster",
            vpc=vpc,
            cluster_name="real-estate-semantic-search-cluster",
            container_insights=True,
        )

        prefix_list_call = custom_resources.AwsSdkCall(
            service="EC2",
            action="describeManagedPrefixLists",
            parameters={
                "Filters": [
                    {
                        "Name": "prefix-list-name",
                        "Values": ["com.amazonaws.global.cloudfront.origin-facing"],
                    }
                ]
            },
            output_paths=["PrefixLists.0.PrefixListId"],
            physical_resource_id=custom_resources.PhysicalResourceId.of(
                "CloudFrontOriginFacingPrefixList"
            ),
        )
        prefix_list_lookup = custom_resources.AwsCustomResource(
            self,
            "CloudFrontPrefixListLookup",
            on_create=prefix_list_call,
            on_update=prefix_list_call,
            policy=custom_resources.AwsCustomResourcePolicy.from_sdk_calls(
                resources=custom_resources.AwsCustomResourcePolicy.ANY_RESOURCE
            ),
        )
        prefix_list_id = prefix_list_lookup.get_response_field(
            "PrefixLists.0.PrefixListId"
        )

        alb_security_group = ec2.SecurityGroup(
            self,
            "ApplicationLoadBalancerSecurityGroup",
            vpc=vpc,
            description="Accepts HTTP only from CloudFront VPC origin infrastructure",
            allow_all_outbound=True,
        )
        alb_security_group.add_ingress_rule(
            ec2.Peer.prefix_list(prefix_list_id),
            ec2.Port.tcp(80),
            "CloudFront origin-facing managed prefix list",
        )
        ecs_security_group = ec2.SecurityGroup(
            self,
            "FrontendTaskSecurityGroup",
            vpc=vpc,
            description="Accepts Streamlit traffic only from the internal load balancer",
            allow_all_outbound=True,
        )
        ecs_security_group.add_ingress_rule(
            alb_security_group,
            ec2.Port.tcp(8501),
            "Internal load balancer to Streamlit",
        )
        loader_security_group = ec2.SecurityGroup(
            self,
            "DataLoaderSecurityGroup",
            vpc=vpc,
            description="Network boundary for the one-off synthetic data loader",
            allow_all_outbound=True,
        )
        ec2.CfnSecurityGroupIngress(
            self,
            "FrontendToOpenSearchIngress",
            group_id=backend_stack.opensearch_security_group.security_group_id,
            ip_protocol="tcp",
            from_port=443,
            to_port=443,
            source_security_group_id=ecs_security_group.security_group_id,
            description="Authenticated frontend searches",
        )
        ec2.CfnSecurityGroupIngress(
            self,
            "LoaderToOpenSearchIngress",
            group_id=backend_stack.opensearch_security_group.security_group_id,
            ip_protocol="tcp",
            from_port=443,
            to_port=443,
            source_security_group_id=loader_security_group.security_group_id,
            description="One-off data loader",
        )

        access_log_bucket = s3.Bucket(
            self,
            "AccessLogBucket",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            object_ownership=s3.ObjectOwnership.OBJECT_WRITER,
            access_control=s3.BucketAccessControl.LOG_DELIVERY_WRITE,
            lifecycle_rules=[
                s3.LifecycleRule(expiration=Duration.days(30), enabled=True)
            ],
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )

        frontend_log_group = logs.LogGroup(
            self,
            "FrontendLogGroup",
            log_group_name="/ecs/real-estate-semantic-search-frontend",
            retention=logs.RetentionDays.ONE_WEEK,
            removal_policy=RemovalPolicy.DESTROY,
        )
        task_definition = ecs.FargateTaskDefinition(
            self,
            "FrontendTaskDefinition",
            memory_limit_mib=2048,
            cpu=1024,
        )
        container = task_definition.add_container(
            "FrontendContainer",
            image=ecs.ContainerImage.from_asset(
                directory=str(solution_dir / "frontend")
            ),
            memory_limit_mib=2048,
            logging=ecs.LogDrivers.aws_logs(
                stream_prefix="real-estate-semantic-search",
                log_group=frontend_log_group,
            ),
            environment={
                "AWS_REGION": self.region,
                "COGNITO_REGION": self.region,
                "COGNITO_USER_POOL_ID": backend_stack.user_pool.user_pool_id,
                "COGNITO_APP_CLIENT_ID": backend_stack.user_pool_client.user_pool_client_id,
                "COGNITO_IDENTITY_POOL_ID": backend_stack.identity_pool.ref,
                "OPENSEARCH_ENDPOINT": (
                    f"https://{backend_stack.opensearch_domain.domain_endpoint}"
                ),
                "INDEX_NAME": "real-estate-properties",
                "PLACE_INDEX_NAME": backend_stack.place_index.index_name,
                "SEMANTIC_SEARCH_DEBUG": "false",
            },
            health_check=ecs.HealthCheck(
                command=[
                    "CMD-SHELL",
                    "python -c \"import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health', timeout=4)\" || exit 1",
                ],
                interval=Duration.seconds(30),
                timeout=Duration.seconds(5),
                retries=3,
                start_period=Duration.seconds(60),
            ),
        )
        container.add_port_mappings(
            ecs.PortMapping(container_port=8501, protocol=ecs.Protocol.TCP)
        )

        alb = elbv2.ApplicationLoadBalancer(
            self,
            "InternalApplicationLoadBalancer",
            vpc=vpc,
            internet_facing=False,
            load_balancer_name="real-estate-semantic-search-alb",
            security_group=alb_security_group,
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
            ),
            drop_invalid_header_fields=True,
        )
        alb.log_access_logs(access_log_bucket, prefix="alb")
        target_group = elbv2.ApplicationTargetGroup(
            self,
            "FrontendTargetGroup",
            port=8501,
            protocol=elbv2.ApplicationProtocol.HTTP,
            vpc=vpc,
            target_type=elbv2.TargetType.IP,
            health_check=elbv2.HealthCheck(
                enabled=True,
                healthy_http_codes="200",
                path="/_stcore/health",
                protocol=elbv2.Protocol.HTTP,
                timeout=Duration.seconds(10),
                interval=Duration.seconds(30),
                healthy_threshold_count=2,
                unhealthy_threshold_count=5,
            ),
            deregistration_delay=Duration.seconds(30),
        )
        # This non-production sample accepts the private VPC-origin CloudFront-to-ALB
        # hop over HTTP for simplicity. End-to-end TLS needs a private domain, ACM or
        # private certificate, HTTPS listener, and HTTPS-only origin protocol policy.
        # Cognito uses USER_PASSWORD_AUTH. SRP is a stronger production choice.
        alb.add_listener(
            "HttpListener",
            port=80,
            protocol=elbv2.ApplicationProtocol.HTTP,
            default_target_groups=[target_group],
            open=False,
        )

        vpc_origin = origins.VpcOrigin.with_application_load_balancer(
            alb,
            protocol_policy=cloudfront.OriginProtocolPolicy.HTTP_ONLY,
            http_port=80,
            vpc_origin_name="real-estate-semantic-search-origin",
        )
        behavior = cloudfront.BehaviorOptions(
            origin=vpc_origin,
            viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
            allowed_methods=cloudfront.AllowedMethods.ALLOW_ALL,
            cached_methods=cloudfront.CachedMethods.CACHE_GET_HEAD_OPTIONS,
            cache_policy=cloudfront.CachePolicy.CACHING_DISABLED,
            origin_request_policy=cloudfront.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER,
            compress=True,
        )
        distribution_tls_options = {}
        if certificate:
            distribution_tls_options = {
                "certificate": certificate,
                "domain_names": [domain_name],
                "ssl_support_method": cloudfront.SSLMethod.SNI,
                "minimum_protocol_version": (
                    cloudfront.SecurityPolicyProtocol.TLS_V1_2_2021
                ),
            }
        distribution = cloudfront.Distribution(
            self,
            "RealEstateSemanticSearchDistribution",
            default_behavior=behavior,
            additional_behaviors={
                "/_stcore/*": cloudfront.BehaviorOptions(
                    origin=vpc_origin,
                    viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                    cache_policy=cloudfront.CachePolicy.CACHING_DISABLED,
                    origin_request_policy=cloudfront.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER,
                    allowed_methods=cloudfront.AllowedMethods.ALLOW_ALL,
                ),
                "/stream/*": cloudfront.BehaviorOptions(
                    origin=vpc_origin,
                    viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                    cache_policy=cloudfront.CachePolicy.CACHING_DISABLED,
                    origin_request_policy=cloudfront.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER,
                    allowed_methods=cloudfront.AllowedMethods.ALLOW_ALL,
                ),
            },
            price_class=cloudfront.PriceClass.PRICE_CLASS_100,
            enable_logging=True,
            log_bucket=access_log_bucket,
            log_file_prefix="cloudfront/",
            comment="Non-production semantic-search sample",
            **distribution_tls_options,
        )
        application_hostname = domain_name or distribution.distribution_domain_name
        container.add_environment(
            "STREAMLIT_BROWSER_SERVER_ADDRESS",
            application_hostname,
        )

        service = ecs.FargateService(
            self,
            "FrontendService",
            cluster=cluster,
            task_definition=task_definition,
            desired_count=1,
            assign_public_ip=False,
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
            ),
            security_groups=[ecs_security_group],
            service_name="real-estate-semantic-search-service",
            health_check_grace_period=Duration.seconds(120),
        )
        service.attach_to_application_target_group(target_group)
        scaling = service.auto_scale_task_count(min_capacity=1, max_capacity=10)
        scaling.scale_on_cpu_utilization(
            "CpuScaling",
            target_utilization_percent=70,
            scale_in_cooldown=Duration.minutes(5),
            scale_out_cooldown=Duration.minutes(2),
        )
        scaling.scale_on_memory_utilization(
            "MemoryScaling",
            target_utilization_percent=80,
            scale_in_cooldown=Duration.minutes(5),
            scale_out_cooldown=Duration.minutes(2),
        )
        scaling.scale_on_request_count(
            "RequestCountScaling",
            requests_per_target=1000,
            target_group=target_group,
            scale_in_cooldown=Duration.minutes(5),
            scale_out_cooldown=Duration.minutes(2),
        )

        loader_log_group = logs.LogGroup(
            self,
            "DataLoaderLogGroup",
            log_group_name="/ecs/real-estate-semantic-search-loader",
            retention=logs.RetentionDays.ONE_WEEK,
            removal_policy=RemovalPolicy.DESTROY,
        )
        loader_task_definition = ecs.FargateTaskDefinition(
            self,
            "DataLoaderTaskDefinition",
            memory_limit_mib=2048,
            cpu=1024,
            task_role=backend_stack.loader_task_role,
        )
        loader_task_definition.add_container(
            "DataLoaderContainer",
            image=ecs.ContainerImage.from_asset(
                directory=str(solution_dir),
                file="backend/scripts/Dockerfile",
            ),
            logging=ecs.LogDrivers.aws_logs(
                stream_prefix="real-estate-semantic-search-loader",
                log_group=loader_log_group,
            ),
            environment={
                "AWS_REGION": self.region,
                "OPENSEARCH_ENDPOINT": (
                    f"https://{backend_stack.opensearch_domain.domain_endpoint}"
                ),
                "INDEX_NAME": "real-estate-properties",
            },
        )

        private_subnet_ids = [
            subnet.subnet_id
            for subnet in vpc.select_subnets(
                subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
            ).subnets
        ]
        CfnOutput(
            self,
            "RealEstateSemanticSearchApplicationURL",
            value=f"https://{application_hostname}",
            description="Public HTTPS application URL for the sample",
        )
        CfnOutput(
            self,
            "CloudFrontDistributionDomainName",
            value=distribution.distribution_domain_name,
            description="Distribution target for customer-managed DNS",
        )
        CfnOutput(self, "DataLoaderClusterName", value=cluster.cluster_name)
        CfnOutput(
            self,
            "DataLoaderTaskDefinitionArn",
            value=loader_task_definition.task_definition_arn,
        )
        CfnOutput(
            self,
            "DataLoaderSubnetIds",
            value=cdk.Fn.join(",", private_subnet_ids),
        )
        CfnOutput(
            self,
            "DataLoaderSecurityGroupId",
            value=loader_security_group.security_group_id,
        )

        self.load_balancer = alb
        self.ecs_service = service
        self.cluster = cluster
        self.vpc = vpc
        self.target_group = target_group
        self.cloudfront_distribution = distribution
