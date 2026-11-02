# Public versions

[简体中文](../版本与分支.md) · [Home](../README.en.md)

The public repository starts with a sanitized source snapshot and new history. Private development backup locations, work-specific test history, Git bundles and local runtime data are not included.

## v0.1.0 — source release

The first release provides the local download workspace, command-line entry, dedicated/generic routing and experimental resolve-only plans. It adds bilingual documentation, UI screenshots, a local workflow animation and an explicit support matrix.

This is not a portable EXE. See [installation](usage.en.md) and the [changelog](../CHANGELOG.md).

## Entry points

- `start_web.cmd` / `download.cmd`: resolve, download and merge on the user's computer.
- `start_resolver.cmd`: experimental metadata resolution; `download_plan.cmd` consumes exported plans on the user's computer.

Accounts, allowed network traffic and failure behavior differ between the modes. Local download success is not evidence of cloud extraction or cross-public-IP compatibility.

## Public-source checks

Examples use synthetic identifiers and local fixtures. The repository keeps the MIT license, third-party notices and verified HTTPS certificate checks. Before publishing changes, check that `.auth`, `.web`, logs, videos and personal configuration are not tracked. Feature branches are recommended for development before merging into the main branch.
