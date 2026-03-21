"""Message entropy monitoring utilities for AI Agent Hub."""
from __future__ import annotations

import math
import random
import re
from collections import Counter, defaultdict
from typing import DefaultDict

DIVERGENT_CONTEXTS = (
    "Consider an alternative perspective: what if the opposite were true?",
    "Challenge assumption: what evidence would disprove this conclusion?",
    "Introduce entropy: consider a completely different approach.",
    "Devil's advocate: what are the strongest counterarguments?",
    "Ground truth check: verify this against external factual sources.",
)

_TOKEN_PATTERN = re.compile(r"\b\w+\b", re.UNICODE)


class EntropyMonitor:
    """Track message diversity per thread using normalized Shannon entropy."""

    def __init__(self) -> None:
        self._messages_by_thread: DefaultDict[str, list[str]] = defaultdict(list)

    def add_message(self, thread_id: str, content: str) -> None:
        """Store a message for the given thread."""
        self._messages_by_thread[thread_id].append(content)

    def get_entropy(self, thread_id: str) -> float:
        """Return normalized entropy for the thread in the range [0.0, 1.0]."""
        messages = self._messages_by_thread.get(thread_id, [])
        if not messages:
            return 0.0

        tokens: list[str] = []
        for message in messages:
            tokens.extend(token.lower() for token in _TOKEN_PATTERN.findall(message))

        if not tokens:
            return 0.0

        frequencies = Counter(tokens)
        vocabulary_size = len(frequencies)
        if vocabulary_size <= 1:
            return 0.0

        total = len(tokens)
        entropy = -sum(
            (count / total) * math.log2(count / total)
            for count in frequencies.values()
        )
        max_entropy = math.log2(vocabulary_size)
        if max_entropy == 0:
            return 0.0

        normalized = entropy / max_entropy
        repetition_penalty = vocabulary_size / total
        diversity_score = normalized * repetition_penalty
        return max(0.0, min(1.0, diversity_score))

    def is_low_entropy(self, thread_id: str, threshold: float = 0.3) -> bool:
        """Return whether the thread entropy is at or below the threshold."""
        return self.get_entropy(thread_id) <= threshold

    def inject_divergent_context(self, thread_id: str) -> str:
        """Return a random divergence prompt for the thread."""
        del thread_id  # reserved for future per-thread strategies
        return random.choice(DIVERGENT_CONTEXTS)

    def check_and_inject(self, thread_id: str, threshold: float = 0.3) -> str | None:
        """Return divergent context when entropy is low; otherwise None."""
        if self.is_low_entropy(thread_id, threshold=threshold):
            return self.inject_divergent_context(thread_id)
        return None
