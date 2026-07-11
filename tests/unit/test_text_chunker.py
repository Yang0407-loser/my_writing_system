"""测试文本分块器。"""

from app.utils.text_chunker import chunk_text


class TestChunkText:
    def test_short_text_single_chunk(self):
        result = chunk_text("短短一段话。", chunk_size=500, overlap=100)
        assert len(result) == 1
        assert result[0] == "短短一段话。"

    def test_paragraph_boundary_chunking(self):
        text = "第一段。" * 100 + "\n\n" + "第二段。" * 100
        result = chunk_text(text, chunk_size=300, overlap=50)
        assert len(result) >= 2

    def test_overlap_between_chunks(self):
        text = "零一二三四五六七八九。" * 60
        result = chunk_text(text, chunk_size=200, overlap=50)
        assert len(result) >= 2
        # 前一块的末尾应出现在后一块的开头
        prev_end = result[0][-10:]
        next_start = result[1][:10]
        assert any(c in next_start for c in prev_end if c not in "。；，！？")

    def test_empty_text(self):
        result = chunk_text("", chunk_size=500, overlap=100)
        assert result == [""]

    def test_exact_chunk_boundary(self):
        text = "A" * 500
        result = chunk_text(text, chunk_size=500, overlap=100)
        assert len(result) == 1
