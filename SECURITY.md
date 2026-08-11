# Security Policy

OpenTor MCP controls a browser, processes hostile web content, and stores session cookies. Security
reports are welcome and should be handled privately.

## Supported versions

| Version | Supported |
| --- | --- |
| `0.1.x` | Yes |
| Earlier versions | No |

Until the project reaches a stable release, security fixes are made on the latest `main` branch.

## Report a vulnerability

Use GitHub's private vulnerability reporting flow:

<https://github.com/Medamine-cheddadi/opentor-mcp/security/advisories/new>

Please do not open a public issue for an unpatched vulnerability. Include:

- the affected version or commit;
- reproduction steps or a minimal proof of concept;
- the expected impact;
- any suggested mitigation;
- whether the report contains sensitive data.

The maintainer aims to acknowledge reports within seven days. This is a volunteer side project, so
resolution times depend on severity and availability. Please allow reasonable time for a fix before
public disclosure.

## In scope

- URL-policy or local-network bypasses;
- archive or session path escapes and symlink attacks;
- exposure of cookies, control credentials, or local files;
- prompt-injection boundary failures that bypass documented safeguards;
- MCP result-size or lifecycle issues that cause material denial of service;
- dependency or packaging problems that expose private repository artifacts.

## Out of scope

- anonymity claims the project explicitly does not make;
- availability of third-party onion services or search engines;
- OCR accuracy or unsupported interactive challenge systems;
- attacks requiring prior control of the same trusted local user account;
- unlawful testing of systems without authorization.

Never include real credentials, session cookies, personal data, or illegally obtained content in a
security report.
