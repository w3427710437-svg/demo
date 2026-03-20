import re


# 支持常见分隔符：
# - 标点：中文逗号/英文逗号/顿号/斜杠/半角分号/全角分号
# - 空白：换行、制表符、半角空格、全角空格（U+3000）
_SPLIT_PATTERN = re.compile(r"[，,、/;；\n\t \u3000]+")


def parse_keywords(raw_text: str) -> list[str]:
    items = _SPLIT_PATTERN.split(raw_text.strip())
    return [item.strip() for item in items if item and item.strip()]

