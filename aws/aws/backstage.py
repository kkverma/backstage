from aws_cdk import (
    RemovalPolicy,
    aws_ec2 as ec2,
    aws_ecs as ecs,
    aws_elasticloadbalancingv2 as elbv2,
    aws_iam as iam,
    aws_s3 as s3,
    aws_rds as rds,
    Stack, CfnOutput, Duration
)
from constructs import Construct

class BackstageEcsStack(Stack):
    def __init__(self, scope: Construct, id: str, **kwargs) -> None:
        super().__init__(scope, id, **kwargs)

        # ✅ Create VPC
        vpc = ec2.Vpc(self, "BackstageVpc", max_azs=2)

        # ✅ Security Group for ECS Tasks
        ecs_sg = ec2.SecurityGroup(self, "EcsSecurityGroup", vpc=vpc, allow_all_outbound=True)

        # ✅ Security Group for ALB
        alb_sg = ec2.SecurityGroup(self, "ALBSecurityGroup", vpc=vpc)
        alb_sg.add_ingress_rule(ec2.Peer.any_ipv4(), ec2.Port.tcp(80), "Allow HTTP")

        # ✅ Security Group for RDS
        rds_sg = ec2.SecurityGroup(self, "RdsSecurityGroup", vpc=vpc, allow_all_outbound=True)
        rds_sg.add_ingress_rule(ecs_sg, ec2.Port.tcp(5432), "Allow ECS to connect to RDS")

        # ✅ Create ALB
        alb = elbv2.ApplicationLoadBalancer(self, "CustomALB",
            vpc=vpc,
            internet_facing=True,
            security_group=alb_sg
        )

        # ✅ ALB Listener
        listener = alb.add_listener("ALBListener", port=80, open=True)

        # ✅ ECS Cluster
        cluster = ecs.Cluster(self, "BackstageCluster", vpc=vpc)

        # ✅ S3 Bucket for Backstage Assets
        bucket = s3.Bucket(self, "BackstageAssets", public_read_access=False)

        # ✅ Aurora PostgreSQL Serverless v2
        db_cluster = rds.DatabaseCluster(self, "BackstageAuroraDB",
            engine=rds.DatabaseClusterEngine.aurora_postgres(version=rds.AuroraPostgresEngineVersion.VER_13_12),
            security_groups=[rds_sg],
            credentials=rds.Credentials.from_generated_secret("postgres"),
            default_database_name="backstage",
            removal_policy=RemovalPolicy.DESTROY,
            instance_props=rds.InstanceProps(
                instance_type=ec2.InstanceType.of(ec2.InstanceClass.BURSTABLE3, ec2.InstanceSize.MEDIUM),
                vpc=vpc,
                vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS)
            ),
            serverless_v2_min_capacity=0.5,
            serverless_v2_max_capacity=4
        )

        # ✅ IAM Role for ECS Task
        task_role = iam.Role(self, "BackstageTaskRole",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonS3ReadOnlyAccess")
            ]
        )

        # ✅ IAM Role for Task Execution (ECR Access)
        task_execution_role = iam.Role(self, "BackstageTaskExecutionRole",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com")
        )

        # ✅ Grant permission to pull images from ECR (Restrict to ECR Actions)
        task_execution_role.add_to_policy(iam.PolicyStatement(
            actions=["ecr:GetAuthorizationToken", "ecr:BatchCheckLayerAvailability", "ecr:GetDownloadUrlForLayer", "ecr:BatchGetImage"],
            resources=["*"]
        ))

        # ✅ Define Fargate Task Definition
        task_definition = ecs.FargateTaskDefinition(self, "BackstageTaskDef",
            memory_limit_mib=1024,
            cpu=512,
            task_role=task_role,
            execution_role=task_execution_role,
            runtime_platform=ecs.RuntimePlatform(
                cpu_architecture=ecs.CpuArchitecture.ARM64,  # ✅ Use ARM64 as per your platform
                operating_system_family=ecs.OperatingSystemFamily.LINUX
            )
        )

        # ✅ Define Container
        container = task_definition.add_container("BackstageContainer",
            image=ecs.ContainerImage.from_registry("975050238273.dkr.ecr.ap-south-1.amazonaws.com/backstage"),
            logging=ecs.LogDrivers.aws_logs(stream_prefix="Backstage"),
            environment={
                "POSTGRES_HOST": db_cluster.cluster_endpoint.hostname,
                "POSTGRES_USER": "postgres",
                "POSTGRES_PORT": str(db_cluster.cluster_endpoint.port),
                "AWS_S3_BUCKET": bucket.bucket_name,
                "BASE_URL": f"http://{alb.load_balancer_dns_name}"
            },
            secrets={
                "POSTGRES_PASSWORD": ecs.Secret.from_secrets_manager(db_cluster.secret, field="password")
            },
            port_mappings=[ecs.PortMapping(container_port=7007)]
        )

        # ✅ Create Fargate Service (WITHOUT creating ALB)
        fargate_service = ecs.FargateService(self, "BackstageService",
            cluster=cluster,
            task_definition=task_definition,
            security_groups=[ecs_sg],
            desired_count=1,
            deployment_controller=ecs.DeploymentController(type=ecs.DeploymentControllerType.ECS)
        )

        # ✅ Attach the ECS Service to the ALB Listener
        listener.add_targets("ECS",
            port=80,
            targets=[fargate_service.load_balancer_target(container_name="BackstageContainer", container_port=7007)],
            health_check=elbv2.HealthCheck(
                path="/",
                interval=Duration.seconds(30),
                timeout=Duration.seconds(5),
                healthy_threshold_count=2,
                unhealthy_threshold_count=5
            )
        )

        # ✅ Ensure DB is created before starting ECS
        fargate_service.node.add_dependency(db_cluster)

        # ✅ Output ALB DNS
        CfnOutput(self, "LoadBalancerDNS", value=alb.load_balancer_dns_name)
