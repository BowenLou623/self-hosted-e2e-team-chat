# Team Chat Launcher

Team Chat Launcher is an open-source, self-hosted, local-first team chat prototype for LAN and small-team collaboration.

本项目是一个公益开源、自托管、端对端加密设计演示性质的局域网/小团队协作聊天工具，适合学习、研究、内网实验和合法的自部署场景。

## Important Notice

This project is released as an open-source self-hosted tool. The maintainers do not provide a public messaging service, hosted relay, paid subscription, or managed communication platform. Users are responsible for complying with the laws and regulations of their own jurisdiction.

本项目作为公益开源的自托管工具发布。维护者不提供面向公众的通信运营服务、托管中继服务、付费订阅服务或代运营平台。使用者应自行确保其部署和使用方式符合所在地法律法规。

Do not use this project for illegal activity, malicious attacks, regulatory evasion, spreading illegal content, or claiming absolute anonymity, absolute security, or untraceability.

## Current Status

Status: research prototype / beta.

This codebase is suitable for learning and controlled self-hosted experiments. It is not a formally audited secure messenger, not a managed SaaS product, and not a replacement for enterprise compliance communication systems.

## What Is Included

```text
.
├── README.md
├── LICENSE
├── DISCLAIMER.md
├── PRIVACY.md
├── SECURITY.md
├── requirements.txt
├── config.example.json
└── src/
    ├── app/
    ├── chat/
    ├── config/
    ├── core/
    ├── crypto/
    ├── files/
    ├── hub/
    ├── identity/
    ├── macos/
    ├── models/
    ├── network/
    ├── storage/
    ├── sync/
    ├── transport/
    ├── ui/
    └── utils/
```

Only source code, runtime dependencies, an example config, and necessary project notices are included. Local runtime data, databases, logs, deployment credentials, payment configuration, website files, and private service configuration are intentionally excluded.

## Features

- Python/PySide6 desktop chat client.
- Local Hub for self-hosted LAN or small-team message routing.
- End-to-end encryption design demo for direct and group messages.
- Manual contact trust and local identity handling.
- Local SQLite persistence controlled by the user or deployer.
- Temporary file handoff support.
- Optional macOS SwiftUI launcher under `src/macos/TeamChatLauncher`.
- No public hosted service, no public relay, no paid subscription, no payment flow.

## Requirements

- Python 3.11 or newer is recommended.
- macOS, Linux, or another desktop environment that can run Python and PySide6.
- For the optional macOS launcher: Xcode command line tools / Swift Package Manager.
- A private LAN or local machine for testing.

## Install

Create a virtual environment:

```bash
python3 -m venv .venv
```

Activate it:

```bash
. .venv/bin/activate
```

Install dependencies:

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## Run Locally

Start a local Hub:

```bash
python -m src.network.hub --host 127.0.0.1 --port 8080 --hub-dir runtime/hub
```

Start one client:

```bash
python -m src.app.main --profile alice --transport network --hub 127.0.0.1:8080
```

Start another client in a second terminal:

```bash
python -m src.app.main --profile bob --transport network --hub 127.0.0.1:8080
```

The default commands bind to `127.0.0.1`, which keeps the Hub on the local machine. For LAN testing, replace `127.0.0.1` with a private LAN address only after reviewing firewall exposure.

## Optional macOS Launcher

The optional SwiftUI launcher is included for local profile and service management:

```bash
cd src/macos/TeamChatLauncher
swift run TeamChatLauncher
```

The launcher is optional. The Python Hub and client commands above are the primary way to run the project.

## Configuration

`config.example.json` shows the shape of a local configuration file. Copy it only for local use:

```bash
cp config.example.json config.local.json
```

Do not commit `config.local.json`, `.env` files, local databases, logs, runtime profiles, private keys, API keys, or screenshots containing secrets.

## Runtime Data

When running locally, the application may create runtime data such as:

- `runtime/hub/`
- `runtime/profiles/<profile>/`
- SQLite databases
- local identity files
- local device keys
- logs
- temporary files

These files can contain message data, metadata, contacts, local identities, keys, or private operational details. They are not part of the open-source release and should not be uploaded to GitHub.

## Self-Hosting Notes

- Prefer local-only or private LAN deployment.
- Keep the Hub bound to `127.0.0.1` unless LAN access is intentionally required.
- If LAN access is required, use a firewall and allow only trusted devices.
- Do not expose the Hub directly to the public internet without a full security and compliance review.
- The maintainers do not operate public relay servers and do not provide hosted chat services.
- If you provide access to other people, you are responsible for privacy notices, consent, retention, deletion, security, and legal compliance.

## Security Notes

This project demonstrates an end-to-end encryption design, but it does not guarantee absolute security. It has not been formally verified or independently audited as a high-risk secure messenger.

Important limitations:

- Compromised devices can expose local messages and keys.
- Backups may copy sensitive local databases and keys.
- Metadata such as device presence, connection timing, IP addresses, and file sizes may still exist.
- Local logs may reveal operational details.
- Multi-device and sync workflows require careful key and data handling.

See `SECURITY.md` for vulnerability reporting and security guidance.

## Privacy Notes

The open-source project itself does not collect user chat content. In a self-hosted instance, data is controlled by the person or organization that deploys it.

Do not upload real chat data, personal data, private logs, databases, identity files, access tokens, or device keys to GitHub issues or public discussions.

See `PRIVACY.md` for more details.

## Disclaimer

This project is provided only for lawful, self-hosted, educational, research, and internal collaboration scenarios. The maintainers do not provide a public communication service, do not operate a public relay server, and do not store user chat content for public users.

Users and deployers are solely responsible for deployment, operation, security, privacy, and legal compliance in their own jurisdiction. This README and the accompanying disclaimer are not legal advice.

See `DISCLAIMER.md` before using or deploying the project.

## Troubleshooting

If the client cannot connect:

- Confirm the Hub terminal is still running.
- Confirm the client uses the same host and port as the Hub.
- If using LAN mode, confirm the firewall allows the selected port only for trusted devices.
- Try local-only mode first with `127.0.0.1:8080`.

If the UI does not start:

- Confirm the virtual environment is active.
- Reinstall dependencies with `python -m pip install -r requirements.txt`.
- Confirm PySide6 is available with `python -c "import PySide6"`.

If you see stale local data:

- Stop the app.
- Back up only if needed.
- Remove the local `runtime/` profile directory for a fresh local test.

## License

MIT License. See `LICENSE`.
