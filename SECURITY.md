# Security Policy

## Reporting a vulnerability

If you discover a security issue, please **do not open a public issue**. Instead,
report it privately using GitHub's **"Report a vulnerability"** feature (Security tab
→ Report a vulnerability), or contact the maintainer directly. We will acknowledge
your report as soon as possible and work with you on a fix and disclosure timeline.

## API keys & secrets

This application uses an Anthropic API key.

- Your key is stored in a local `.env` file, which is listed in `.gitignore` and
  **must never be committed**.
- `.env.example` is a template and must contain only a **placeholder**, never a real
  key.
- If a real key is ever committed or otherwise exposed, **revoke/rotate it
  immediately** at <https://console.anthropic.com/> and replace it with a new one.
  Assume any key that has been in a repository or shared in plaintext is compromised.
- Never paste real keys into issues, pull requests, logs, or screenshots.

## Data handling

- When you run a diagnosis, relevant excerpts of your case files and any pasted logs
  are sent to the **Anthropic Claude API** for analysis. Review Anthropic's data
  policies before use.
- Do **not** use this tool on confidential, proprietary, export-controlled (e.g.
  ITAR/EAR), or otherwise restricted data.
- The application only **reads** your case files. It only **writes** a file after you
  explicitly approve a diff, and it always creates a timestamped backup first.
- The tool never executes OpenFOAM, solvers, or shell commands on your machine.

## Supported versions

As an early public release, security fixes are applied to the latest `main`. Please
run the most recent version.
