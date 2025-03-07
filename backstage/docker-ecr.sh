# # Login to AWS ECR
aws ecr get-login-password --region ap-south-1 | docker login --username AWS --password-stdin 975050238273.dkr.ecr.ap-south-1.amazonaws.com

# # Create ECR Repository (only once)
# aws ecr create-repository --repository-name backstage

# Build Docker Image
# docker buildx build . -f packages/backend/Dockerfile --tag backstage --no-cache

docker buildx build --platform linux/amd64,linux/arm64 -t 975050238273.dkr.ecr.ap-south-1.amazonaws.com/backstage:latest --push . -f packages/backend/Dockerfile --no-cache
