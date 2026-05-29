# Security

## Reporting Vulnerabilities

Please report suspected security issues privately to the maintainers. Do not open a public issue containing exploit details, private keys, real chat logs, or instructions that would enable abuse.

If no private contact channel is published yet, open a minimal GitHub issue saying that you need a private security contact, without disclosing exploit details.

## Current Security Boundary

This project is a research prototype and design demo for self-hosted local/small-team collaboration. It is not a formally verified secure messenger and has not been represented as a complete Signal/MLS replacement.

The project does not promise absolute security, absolute anonymity, untraceability, or suitability for high-risk scenarios.

## Key Management

- Keep local device keys and runtime profiles private.
- Do not commit `.env` files, runtime folders, local databases, key files, or secrets.
- Rotate credentials if they were exposed in logs, screenshots, chats, or repository history.
- Back up only after understanding the risk of copying local keys and message data.

## Deployment Advice

- Prefer LAN/private-network deployments.
- Bind services to `127.0.0.1` unless LAN access is intentionally required.
- Use a firewall to restrict Hub access.
- Keep operating systems and dependencies updated.
- Review dependency updates and security advisories regularly.
