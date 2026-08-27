# Project philosophy

AMP favors direct, measurable implementations over speculative abstraction. Work
should reduce the combined cost of development time, model latency, and model
usage without hiding correctness or safety tradeoffs.

The practical priorities are:

- Keep protocol and gameplay behavior observable through offline tests and
  server-authoritative live checks.
- Keep model providers behind a narrow interface so planning does not depend on
  a vendor SDK shape.
- Validate model output before it reaches gameplay or packet serialization.
- Prefer bounded work: packet sizes, decompression, path searches, action waits,
  autonomous steps, and model responses all need explicit limits.
- Add abstractions only when an implemented feature creates a concrete boundary
  for them.
