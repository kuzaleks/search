#!/bin/bash
set -euxo pipefail

dnf update -y
dnf install -y docker git

install -d -m 0755 /usr/local/lib/docker/cli-plugins
curl -fsSL \
  https://github.com/docker/compose/releases/latest/download/docker-compose-linux-x86_64 \
  -o /usr/local/lib/docker/cli-plugins/docker-compose
chmod 0755 /usr/local/lib/docker/cli-plugins/docker-compose

buildx_url="$({
  curl -fsSL https://api.github.com/repos/docker/buildx/releases/latest |
    python3 -c 'import json, sys; print(next(asset["browser_download_url"] for asset in json.load(sys.stdin)["assets"] if asset["name"].endswith(".linux-amd64")))'
})"
curl -fsSL "$buildx_url" -o /usr/local/lib/docker/cli-plugins/docker-buildx
chmod 0755 /usr/local/lib/docker/cli-plugins/docker-buildx

systemctl enable --now docker
usermod -aG docker ec2-user

install -d -o ec2-user -g ec2-user -m 0755 /opt/nevis-search
