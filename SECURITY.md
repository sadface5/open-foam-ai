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
- The application only **writes** a file after you explicitly approve a diff, and it
  always creates a timestamped backup first.
- Nothing outside the selected case folder can be read or written.

## Command execution

The assistant **can run OpenFOAM utilities on your machine** (for example `checkMesh`,
`blockMesh`, `foamDictionary`). This is constrained structurally, not by prompt
instructions:

- **Allowlist.** Only recognised OpenFOAM utilities may run. `bash`, `rm`, `curl`,
  `python` and anything unrecognised are refused, so text found in a log file or
  dictionary cannot become a command.
- **No shell.** Commands are executed as argument lists (`shell=False`), so a case
  path containing `;` or `&&` is treated as data, never as syntax.
- **Writes are gated.** Utilities that modify a case (`blockMesh`, `decomposePar`,
  `setFields`, …) require explicit approval each session.
- **Solvers are supervised.** They cannot be launched by the general command path
  and always run under a time limit.
- **Timeouts** apply to everything, and output is truncated.

The autonomous debugging loop is **read-only by default**, has a hard iteration cap,
records every action, and stops rather than repeating a fix that already failed. It
never runs a solver or modifies a case unless you enable it.

If you would rather the tool never executed anything, simply do not install OpenFOAM
where the app can reach it — with no installation detected, it falls back to
read-only analysis and says so.

## Supported versions

As an early public release, security fixes are applied to the latest `main`. Please
run the most recent version.
