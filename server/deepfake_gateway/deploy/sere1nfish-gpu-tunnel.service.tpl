[Unit]
Description=Sere1nFish GPU private transport and build proxy
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/bin/ssh -NT -p __GPU_SSH_PORT__ -i __GPU_SSH_KEY__ -o BatchMode=yes -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -o StrictHostKeyChecking=yes -L __LOCAL_TUNNEL_HOST__:18443:127.0.0.1:443 -R 127.0.0.1:18443:127.0.0.1:443 __PROXY_FORWARD__ __GPU_SSH_USER__@__GPU_SSH_HOST__
Restart=always
RestartSec=5
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
