"""Restoration of pseudonymisation tokens in an LLM answer (US 07 T3, T5).

Shared by the LLM gateway and, later, by the RAG agent adapters: only the tokens
present in what was actually sent to the provider are restored.
"""

import re
import secrets

from app.services.pseudonymization import PSEUDONYMISED_TYPES, PseudonymVault

ANY_TOKEN = re.compile(
    r"\[(?:" + "|".join(t.value for t in PSEUDONYMISED_TYPES) + r")_\d+_[0-9a-f]{32}\]"
)


def restore_sent_tokens(vault: PseudonymVault, answer: str, prompt: str) -> tuple[str, int, int]:
    """Restore, in one ``restore`` call, only the tokens present in the prompt sent.

    Each sent token is framed by random markers, so that the tokens ``restore``
    actually replaced can be counted; any other token is swapped for a random
    placeholder that ``restore`` cannot match, then put back literally.
    """
    sent = set(ANY_TOKEN.findall(prompt))
    nonce = secrets.token_hex(16)
    while nonce in answer:
        nonce = secrets.token_hex(16)
    framed: list[str] = []
    withheld: list[str] = []

    def mark(match: re.Match[str]) -> str:
        token = match.group()
        if token in sent:
            framed.append(token)
            index = len(framed) - 1
            return f"\ue000{nonce}:{index}\ue001{token}\ue002{nonce}:{index}\ue003"
        withheld.append(token)
        return f"\ue004{nonce}:{len(withheld) - 1}\ue005"

    text = vault.restore(ANY_TOKEN.sub(mark, answer))
    restored = 0
    for index, token in enumerate(framed):
        opening = f"\ue000{nonce}:{index}\ue001"
        closing = f"\ue002{nonce}:{index}\ue003"
        start = text.index(opening)
        end = text.index(closing, start)
        value = text[start + len(opening) : end]
        restored += value != token
        text = text[:start] + value + text[end + len(closing) :]
    for index, token in enumerate(withheld):
        text = text.replace(f"\ue004{nonce}:{index}\ue005", token)
    return text, restored, len(withheld)
