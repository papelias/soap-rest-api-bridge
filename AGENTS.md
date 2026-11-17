# Repository guidance

- This is an MVP portfolio project. Prefer simple, explainable code.
- Introduce abstractions only when they solve an existing requirement.
- Add dependencies only for a concrete reason.
- Preserve boundaries between API, SOAP, NL, and policy modules.
- Make all external I/O replaceable in tests.
- New behavior requires tests.
- Use async APIs for network I/O.
- Never commit API keys or secrets.
- Never use IBM code, data, client names, internal terminology, architecture, or proprietary material.
- Use public services and public documentation only.
- Do not modify scope unless explicitly requested or implement future milestones early.
- The developer must be able to explain every design decision in an interview.
