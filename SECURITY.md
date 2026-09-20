# Security Policy

Kindle Book Converter is a **local desktop application**: PDFs are processed on
your own machine and are never uploaded to a remote service. The application
does not include telemetry, auto-updates, or cloud components.

## Reporting a vulnerability

If you believe you have found a security-relevant issue:

- **Do not publicly disclose the details** before the maintainers have had a
  chance to review them.
- If the repository exposes GitHub's **private vulnerability reporting**
  (GitHub → **Security** tab → *Reporting* on this repository), use it.
  This repository does not publish a dedicated security contact address.
- Otherwise, open an issue using the [bug report template](
  .github/ISSUE_TEMPLATE/bug_report.yml) and keep the report focused on the
  technical finding.
- **Do not attach private PDFs, personal documents, or personal data** to a
  report. If a reproduction file is needed, use a small non-sensitive
  placeholder document.

## What to include in a report

- The application version (`CHANGELOG.md` head entry, or the version shown by
  the packaged application) and how it was run (packaged Windows executable or
  from source).
- The Windows version.
- Steps to reproduce, and expected vs. actual behavior.
- Relevant error or log output, with any local paths or personal information
  removed.

## Security support

The project is pre-1.0 (`0.1.0`). Security support is best-effort and applies
to the most recent release; older releases are not separately maintained.