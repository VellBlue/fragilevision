# Security policy

## Threat model

FragileVision is a single-user local research tool. It is not a multi-tenant service and must not be exposed directly to the internet.

The application binds to loopback only, does not enable CORS, requires a random per-process token for mutations and rejects public model endpoints. These controls reduce browser-based cross-origin mutation and accidental data exfiltration.

Model requests bypass operating-system HTTP proxies, validate every resolved address and revalidate redirect destinations. This prevents a nominally local provider from silently forwarding image payloads to a public host. Private DNS and Tailscale names must resolve exclusively to loopback, private, link-local or Tailscale-range addresses.

## Data at rest

Version 0.2 stores managed image copies, annotations and raw model responses locally without application-level encryption. Use FileVault, LUKS or another full-disk encryption mechanism when evaluating sensitive material.

Replay Bundles exclude image pixels but include prompt text, annotations and raw model output. Review a bundle before publishing it.

## Reporting a vulnerability

Do not open a public issue for a vulnerability that could expose local files or bypass endpoint restrictions. Contact the maintainer privately at **s.a.zito-dev@proton.me** and include a minimal reproduction, affected version and expected impact.
