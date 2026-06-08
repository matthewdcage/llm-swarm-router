# Security policy

## Supported versions

Security fixes are applied to the latest release on `main` and backported to the
most recent tagged release when practical.

| Version | Supported |
|---------|-----------|
| latest `main` | yes |
| latest GitHub Release | yes |
| older releases | best effort |

Install updates from [GitHub Releases](https://github.com/matthewdcage/llm-swarm-router/releases)
or rebuild from `main` if you run from source.

## Reporting a vulnerability

**Please do not open a public GitHub issue for security vulnerabilities.**

Instead, report privately by one of:

1. **GitHub Security Advisories**, use
   [Report a vulnerability](https://github.com/matthewdcage/llm-swarm-router/security/advisories/new)
   on the repository (preferred).
2. **Email**, contact the maintainer via the address listed on their
   [GitHub profile](https://github.com/matthewdcage) or a private message on GitHub.

Include as much detail as you can:

- Description of the issue and impact
- Steps to reproduce
- Affected version(s) and platform(s)
- Proof-of-concept or exploit details (if available)
- Suggested fix (optional)

## What we consider in scope

Examples of issues we want to hear about:

- Remote code execution or authentication bypass on the agent HTTP API
- Unauthorized LAN access when `serve --host 0.0.0.0` is used without
  `swarm.cluster_token` on untrusted networks
- Secrets or credentials committed to the repository
- Supply-chain issues in release artifacts (DMG, deb, rpm, zip)
- Tampered or mismatched update downloads (menubar verifies SHA256 sidecars when present)
- mDNS / swarm peer spoofing that leads to request hijacking

## Out of scope (generally)

- Denial of service against a single-user local agent on loopback
- Misconfiguration by the operator (e.g. binding `0.0.0.0` on a public network
  without a cluster token), we document safer defaults in
  [CONTRIBUTING.md](CONTRIBUTING.md) and [AGENTS.md](AGENTS.md)
- Vulnerabilities in third-party LLM backends (Ollama, LM Studio, oMLX, vLLM)
  unless netllm introduces a new attack surface

## Response timeline

We aim to:

- Acknowledge reports within **72 hours**
- Provide an initial assessment within **7 days**
- Coordinate disclosure and a fix before public announcement when possible

## Safe defaults reminder

When exposing the agent on a LAN:

- Use `swarm.cluster_token` on untrusted networks
- Prefer loopback (`127.0.0.1`) for single-machine development
- See `./netllm doctor` and [docs/editor-integration.md](docs/editor-integration.md)

Thank you for helping keep llm-swarm-router users safe.
