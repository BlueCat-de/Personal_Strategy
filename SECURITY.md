# Security Policy

## Supported Versions

Security fixes are applied to the latest version on the default branch.

## Reporting a Vulnerability

Do not open a public issue for:

- exposed API tokens or webhook URLs;
- arbitrary file access or command execution;
- dependency vulnerabilities with a practical exploit;
- data-integrity bugs that silently introduce future information.

Report the issue privately through the contact options on the
[BlueCat-de GitHub profile](https://github.com/BlueCat-de). Include affected
versions, reproduction steps, impact, and any suggested remediation.

The maintainer will acknowledge a valid report when practical and coordinate
disclosure after a fix is available.

## Secret Handling

Real credentials belong only in ignored local files:

```text
.env.local
.feishu_webhook
```

If a credential is committed or published, revoke and rotate it immediately.
Removing it from the latest commit is not sufficient because Git history may
still contain the value.

## Financial Safety

This project does not place orders. Integrations that add broker execution must
default to paper trading, require explicit user confirmation, and include
position, cash, price-limit, and duplicate-order controls.
