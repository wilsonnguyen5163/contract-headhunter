"""v1 matching with the Gemini API (doc/V1_PLAN.md).

provider.py   the LLMProvider interface, request/response models and errors
gemini.py     the Gemini REST client (httpx; the only module that calls the network)
usage.py      the per-call usage log and the daily budget guard
payload.py    the pruned job text sent to the API
anonymize.py  the contractor cards sent to the API (no names or contact details)
prompt.py     the prompt, the answer schema and the structural checks on answers
evidence.py   checks that every quoted piece of evidence is in the text that was sent
preview.py    builds requests without sending them (llm-preview)

Nothing here imports the v0 matcher (embed/, match/).
"""
