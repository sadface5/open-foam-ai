"""
Parses Claude's structured response (the required standard format) into a Python
dict. This lets the GUI drive the progress panel (per-step statuses, overall
confidence) and read the "Recommended first change" without re-parsing by hand.

It is tolerant: any missing section simply comes back empty.
"""
import re

# The level-2 headers we expect, in order (used for display / validation).
SECTION_HEADERS = [
    "Selected skill",
    "Case type detected",
    "Files inspected",
    "Files missing",
    "10-step diagnostic summary",
    "Main diagnosis",
    "Overall confidence",
    "Evidence",
    "Issues found",
    "Additional AI findings beyond the 10-step checklist",
    "Recommended first change",
    "Additional changes (only if needed)",
    "What cannot be verified without running OpenFOAM",
    "Files or information needed next",
    "How the user should verify the proposed fix",
    "Sources retrieved from the knowledge base",
]


def _split_sections(text: str) -> dict[str, str]:
    """Split markdown into {lowercased level-2 header: body}."""
    parts = re.split(r"(?m)^##\s+(.+?)\s*$", text)
    sections = {}
    for i in range(1, len(parts), 2):
        header = parts[i].strip().lower()
        body = parts[i + 1] if i + 1 < len(parts) else ""
        sections[header] = body.strip()
    return sections


def _field(body: str, name: str) -> str:
    m = re.search(rf"(?mi)^\s*[-*]?\s*{re.escape(name)}\s*:\s*(.+?)\s*$", body)
    return m.group(1).strip() if m else ""


def _bullets(body: str) -> list[str]:
    out = []
    for line in body.splitlines():
        line = line.strip()
        if line.startswith(("-", "*")):
            out.append(line.lstrip("-* ").strip())
    return out


def _parse_steps(block: str) -> list[dict]:
    steps = []
    parts = re.split(r"(?m)^###\s+Step\s+(\d+)\s*:?\s*(.*)$", block)
    for i in range(1, len(parts), 3):
        num = int(parts[i])
        title = parts[i + 1].strip()
        body = parts[i + 2] if i + 2 < len(parts) else ""
        steps.append({
            "number": num,
            "title": title,
            "finding": _field(body, "Finding"),
            "evidence": _field(body, "Evidence"),
            "status": _field(body, "Status"),
            "confidence": _field(body, "Confidence"),
        })
    return steps


def parse_response(text: str) -> dict:
    s = _split_sections(text)
    return {
        "selected_skill": s.get("selected skill", ""),
        "case_type": s.get("case type detected", ""),
        "files_inspected": _bullets(s.get("files inspected", "")),
        "files_missing": _bullets(s.get("files missing", "")),
        "steps": _parse_steps(s.get("10-step diagnostic summary", "")),
        "main_diagnosis": s.get("main diagnosis", ""),
        "overall_confidence": (s.get("overall confidence", "").splitlines() or [""])[0].strip(),
        "additional_findings": s.get("additional ai findings beyond the 10-step checklist", ""),
        "recommended_first_change": {
            "file": _field(s.get("recommended first change", ""), "File"),
            "current": _field(s.get("recommended first change", ""), "Current entry"),
            "proposed": _field(s.get("recommended first change", ""), "Proposed change"),
            "reason": _field(s.get("recommended first change", ""), "Reason"),
            "verification": _field(s.get("recommended first change", ""), "Verification method"),
        },
        "cannot_verify": s.get("what cannot be verified without running openfoam", ""),
        "needed_next": s.get("files or information needed next", ""),
        "sources": s.get("sources retrieved from the knowledge base", ""),
        "raw": text,
    }
