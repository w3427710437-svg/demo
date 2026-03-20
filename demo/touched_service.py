import re
from collections import deque


def normalize_text(text: str) -> str:
    # 轻量归一化：移除所有空白，保持严格字面匹配。
    s = "".join(text.split())
    # 如果包含英文字符，做小写化以支持大小写差异
    if re.search(r"[A-Za-z]", s):
        s = s.lower()
    return s


class _Node:
    __slots__ = ("next", "fail", "out")

    def __init__(self) -> None:
        self.next: dict[str, int] = {}
        self.fail: int = 0
        self.out: list[int] = []


class AhoCorasickMatcher:
    def __init__(self, patterns: list[str]) -> None:
        self.patterns = patterns
        self.nodes = [_Node()]
        self._build_trie()
        self._build_fail()

    def _build_trie(self) -> None:
        for idx, pat in enumerate(self.patterns):
            cur = 0
            for ch in pat:
                nxt = self.nodes[cur].next.get(ch)
                if nxt is None:
                    nxt = len(self.nodes)
                    self.nodes[cur].next[ch] = nxt
                    self.nodes.append(_Node())
                cur = nxt
            self.nodes[cur].out.append(idx)

    def _build_fail(self) -> None:
        q: deque[int] = deque()
        for _, nxt in self.nodes[0].next.items():
            self.nodes[nxt].fail = 0
            q.append(nxt)

        while q:
            r = q.popleft()
            for ch, u in self.nodes[r].next.items():
                q.append(u)
                v = self.nodes[r].fail
                while v and ch not in self.nodes[v].next:
                    v = self.nodes[v].fail
                self.nodes[u].fail = self.nodes[v].next.get(ch, 0)
                self.nodes[u].out.extend(self.nodes[self.nodes[u].fail].out)

    def match_flags(self, text: str) -> list[bool]:
        flags = [False] * len(self.patterns)
        state = 0
        for ch in text:
            while state and ch not in self.nodes[state].next:
                state = self.nodes[state].fail
            state = self.nodes[state].next.get(ch, 0)
            if self.nodes[state].out:
                for idx in self.nodes[state].out:
                    flags[idx] = True
        return flags


def score_touched(
    transcript_text: str,
    keywords: list[str],
    *,
    aliases_per_keyword: list[list[str]] | None = None,
) -> dict:
    """
    精准触达（Touched）：
    - 默认：原关键词的字面命中（AC 自动机）
    - 若提供 aliases_per_keyword：对未命中的关键词，再做 alias 的字面命中（仍是严格字符串匹配）
    """
    normalized_transcript = normalize_text(transcript_text)
    normalized_keywords = [normalize_text(k) for k in keywords]

    # 1) 原词 AC 精确命中
    valid_indices = [i for i, kw in enumerate(normalized_keywords) if kw]
    valid_patterns = [normalized_keywords[i] for i in valid_indices]

    touched_flags = [False] * len(keywords)
    if valid_patterns:
        matcher = AhoCorasickMatcher(valid_patterns)
        valid_flags = matcher.match_flags(normalized_transcript)
        for pos, original_idx in enumerate(valid_indices):
            touched_flags[original_idx] = valid_flags[pos]

    # 2) 可选：别名精确命中（只补漏，不改已命中的）
    if aliases_per_keyword and len(aliases_per_keyword) == len(keywords):
        for i in range(len(keywords)):
            if touched_flags[i]:
                continue
            aliases = aliases_per_keyword[i] or []
            for alias in aliases:
                alias_norm = normalize_text(alias)
                if not alias_norm:
                    continue
                if alias_norm in normalized_transcript:
                    touched_flags[i] = True
                    break

    return {"total_touched": sum(1 for f in touched_flags if f), "touched_flags": touched_flags}

