"""Stage 4 — conversational demand intake.

slots.py    what a complete demand needs, question wording, defaults, summary
context.py  per-request lookups (skills, accounts, projects, locations)
rules.py    rule-based message understanding (works without any API key)
llm.py      Gemini structured extraction (used when GEMINI_API_KEY is set; falls back to rules)
apply.py    validate + normalise an extracted patch into the draft
graph.py    conversation graph (LangGraph when installed, identical built-in runner otherwise)
service.py  sessions & messages persistence
"""
