# Temporary EC2 Demo Deployment

This deployment runs the API and PostgreSQL together with Docker Compose on a
single EC2 instance. It is intended for a short-lived interview demonstration,
not production use.

## Runtime Shape

- One `t3.small` Amazon Linux 2023 EC2 instance.
- A 20 GB encrypted EBS root volume stores Docker's PostgreSQL volume.
- HTTP port 80 is public.
- SSH port 22 is restricted to the deployer's current public IP.
- PostgreSQL is bound only to the instance loopback interface.
- Docker restart policies restore the API and database after an instance
  reboot.

The instance is bootstrapped by [`deploy/ec2-user-data.sh`](deploy/ec2-user-data.sh).

The API has no authentication by requirement. Keep the deployment online only
for the review window and configure an OpenAI project usage limit.

## Current Deployment

- Deployment date: 2026-08-15.
- AWS Region: `eu-west-2`.
- Instance: `i-02264b00930a3ea06` (`t3.small`).
- Security group: `sg-0a4106fe5e57dc848`.
- EC2 key pair: `nevis-search-demo`.
- Local private key: `~/.ssh/nevis-search-demo`.
- Public host: `18.133.142.221`.
- API documentation: <http://18.133.142.221/docs>.

The public address is not an Elastic IP. It survives an instance reboot but
changes after stopping and starting the instance.

## Deploy

Create a production `.env` on the instance with a random database password and
these settings:

```dotenv
ENVIRONMENT=production
LOG_LEVEL=INFO
OPENAI_API_KEY=...
EMBEDDING_TIMEOUT_SECONDS=30
EMBEDDING_MAX_RETRIES=1
POSTGRES_DB=search
POSTGRES_USER=search
POSTGRES_PASSWORD=...
POSTGRES_PORT=5432
APP_BIND_ADDRESS=0.0.0.0
APP_PORT=80
```

Then start the complete stack:

```bash
docker compose up -d --build
docker compose ps -a
curl http://localhost/ready
```

The migration container should exit with status 0. The database and API
containers should remain healthy.

## Operate

```bash
docker compose ps -a
docker compose logs --tail=200 api
docker compose restart api
docker compose pull
docker compose up -d --build
```

The public demonstration endpoints are:

```text
http://EC2_PUBLIC_HOST/docs
http://EC2_PUBLIC_HOST/health
http://EC2_PUBLIC_HOST/ready
```

## Back Up

For this temporary deployment, create a logical backup before replacing the
instance:

```bash
docker compose exec -T database \
  pg_dump -U search -d search -Fc > search.dump
```

## Tear Down

Terminate the EC2 instance after the review. Its root EBS volume is configured
for deletion on termination. Also delete the dedicated security group and EC2
key pair, then remove the local private key.

This destroys all clients, documents, and embeddings stored by the demo.

For the current deployment:

```bash
aws ec2 terminate-instances \
  --region eu-west-2 \
  --instance-ids i-02264b00930a3ea06
aws ec2 wait instance-terminated \
  --region eu-west-2 \
  --instance-ids i-02264b00930a3ea06
aws ec2 delete-security-group \
  --region eu-west-2 \
  --group-id sg-0a4106fe5e57dc848
aws ec2 delete-key-pair \
  --region eu-west-2 \
  --key-name nevis-search-demo
rm ~/.ssh/nevis-search-demo ~/.ssh/nevis-search-demo.pub
```
