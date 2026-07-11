import re

_CJK_RE = re.compile(r'[一-鿿㐀-䶿豈-﫿]')


def count_chinese_chars(text: str) -> int:
    """统计文本中的中文字符数（不含标点、空格、英文）。"""
    return len(_CJK_RE.findall(text))
