# Security Policy

## Supported versions

Only the latest commit on `main` is supported.

## Reporting a vulnerability

Use GitHub's private vulnerability reporting on this repository (Security tab, "Report a vulnerability"). Include reproduction steps and the affected paths. Do not open a public issue for a vulnerability.

## Scope

The pipeline and the study site in this repository, including the CI leak gates. The maintainer's private deployment and any data it serves are outside the scope of this public repository.

## What the repository already enforces

Every push runs gitleaks and a private denylist scan over the full history in CI (ADR 0044). The site serves only critic-reviewed, labelled material (ADR 0045), and container builds start from a digest-pinned base image and install hash-verified dependencies only.
