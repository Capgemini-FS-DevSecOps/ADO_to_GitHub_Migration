"""Scope guardrails for the migration agent — refuse off-topic and prohibited requests."""
from __future__ import annotations

import re

OUT_OF_SCOPE_REPLY = (
    "I'm the ADO→GitHub migration assistant. I only help with Azure DevOps to GitHub "
    "migrations — discovery, planning, execution, validation, pipelines, secrets, and "
    "related platform tasks. I can't help with that request."
)

PROHIBITED_REPLY = (
    "I can't assist with hacking, bypassing security, credential theft, malware, or other "
    "harmful or illegal activity. This assistant is limited to authorized ADO→GitHub "
    "migration work."
)

_MIGRATION_TERMS = (
    "ado",
    "azure devops",
    "azure",
    "devops",
    "github",
    "gh ",
    " gh",
    "migrate",
    "migration",
    "repo",
    "repository",
    "pipeline",
    "phase",
    "dry-run",
    "dry run",
    "discovery",
    "inventory",
    "secret",
    "service connection",
    "gei",
    "actions",
    "validate",
    "rollback",
    "profile",
    "wave",
    "poc",
    "pilot",
    "branch polic",
    "work item",
    "variable group",
    "pev",
    "remigrate",
    "replan",
)

_IN_SCOPE_META_PHRASES = (
    "hello",
    "hi ",
    " hi",
    "hey",
    "thanks",
    "thank you",
    "what can you do",
    "what do you do",
    "how can you help",
    "who are you",
    "capabilities",
    "help me use",
    "help with migration",
)

_OFF_TOPIC_PHRASES = (
    "recipe",
    "cake",
    "cook ",
    "cooking",
    "bake ",
    "ingredient",
    "restaurant",
    "weather",
    "sports",
    "movie",
    "joke",
    "poem",
    "song",
    "lyrics",
    "search the web",
    "find me a",
    "write me a",
    "tell me a story",
    "stock price",
    "dating",
    "medical advice",
    "legal advice",
    "homework",
    "essay",
)

_PROHIBITED_PHRASES = (
    "hack ",
    "hacking",
    "exploit",
    "malware",
    "ransomware",
    "keylogger",
    "phishing",
    "bypass auth",
    "bypass security",
    "crack password",
    "steal ",
    "ddos",
    "sql injection",
    "xss attack",
    "illegal",
    "launder",
    "counterfeit",
    "weapon",
    "bomb",
)


def is_migration_related(user_message: str) -> bool:
    msg = user_message.lower()
    return any(term in msg for term in _MIGRATION_TERMS)


def is_in_scope_meta(user_message: str) -> bool:
    msg = user_message.lower().strip()
    if not msg:
        return True
    if any(phrase in msg for phrase in _IN_SCOPE_META_PHRASES):
        return True
    if msg in ("hi", "hey", "hello", "help", "thanks"):
        return True
    return False


def is_prohibited_message(user_message: str) -> bool:
    msg = user_message.lower()
    return any(phrase in msg for phrase in _PROHIBITED_PHRASES)


def is_out_of_scope_message(user_message: str) -> bool:
    if is_prohibited_message(user_message):
        return True
    if is_migration_related(user_message):
        return False
    if is_in_scope_meta(user_message):
        return False
    if any(phrase in user_message.lower() for phrase in _OFF_TOPIC_PHRASES):
        return True
    # Long general-knowledge questions without migration context.
    if len(user_message.split()) >= 6 and not is_migration_related(user_message):
        return True
    return False


def scope_refusal_reply(user_message: str) -> str:
    if is_prohibited_message(user_message):
        return PROHIBITED_REPLY
    return OUT_OF_SCOPE_REPLY
