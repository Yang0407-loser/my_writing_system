"""测试中文字数统计。"""

from app.utils.word_counter import count_chinese_chars


class TestCountChineseChars:
    def test_pure_chinese(self):
        assert count_chinese_chars("今天天气很好") == 6

    def test_mixed_cn_en(self):
        assert count_chinese_chars("Hello 你好 World 世界") == 4

    def test_punctuation_not_counted(self):
        assert count_chinese_chars("你好，世界！对吧？") == 6  # 你好世界对吧=6，标点不算

    def test_empty(self):
        assert count_chinese_chars("") == 0

    def test_numbers_not_counted(self):
        assert count_chinese_chars("2024年3月") == 2  # 年和月

    def test_long_text(self):
        text = "这是一个用于测试字数统计功能的较长段落。" * 20
        assert count_chinese_chars(text) == 20 * 19  # 每句 19 个 CJK 字
