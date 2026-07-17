"""
v0.9.2: RAG召回率 & 风格稳定性离线评估脚本

Usage:
    # 评估 RAG 召回率（从 Redis 拉取 rag_recall_log）
    uv run python tests/eval_quality.py rag <task_id>

    # 评估风格稳定性（从 Redis 拉取 style_baseline，用 StyleAnalyzer 重跑终稿对比）
    uv run python tests/eval_quality.py style <task_id>

    # 跑完自动抽样导出，不需要 LLM API key
"""

import json
import sys


def eval_rag_recall(task_id: str) -> None:
    """导出 RAG 召回日志，每小节一条，人工标注 relevancy。"""
    from app.blackboard import Blackboard
    bb = Blackboard()

    raw = bb.get(task_id, "rag_recall_log")
    if not raw:
        print("rag_recall_log 不存在。任务完成后会自动写入。")
        return

    entries = json.loads(raw) if isinstance(raw, str) else raw

    # 统计
    total_recalled = 0
    total_causal = 0

    print(f"=== RAG 召回日志: 共 {len(entries)} 个小节 ===\n")
    print("标注方式：每条召回文本标 [相关] 或 [不相关]")
    print("格式: [sec.sub] query -> 召回了哪些片段\n")

    for e in entries:
        sem_items = e.get("semantic_items", [])
        causal_secs = e.get("causal_sections", [])
        total_recalled += len(sem_items)
        total_causal += len(causal_secs)

        print(f"[{e['section']}.{e['subsection']}] query: {e['query'][:100]}")
        if sem_items:
            for i, item in enumerate(sem_items, 1):
                sec = item.get("section", "?")
                sub = item.get("subsection", 0)
                title = item.get("title", "")
                text = item.get("text", "")
                print(f"    #{i} [S{sec}.{sub}] {title}")
                print(f"       \"{text}\"")
                print(f"       [ ] 相关  [ ] 不相关")
        else:
            print(f"    (无语义召回)")
        if causal_secs:
            print(f"    因果章节: {causal_secs}")
            print(f"       [ ] 漏了 — 应该召回但没召回")
        print()

    print(f"=== 统计 ===")
    print(f"总召回条数: {total_recalled}")
    print(f"因果扩展: {total_causal}")
    print(f"如需 LLM 自动评判: uv run python tests/eval_quality.py rag-auto <task_id>")


def eval_rag_cite(task_id: str) -> None:
    """评估 RAG 引用率：召回文本在最终稿中是否被实际引用。"""
    import json as _j
    from app.blackboard import Blackboard
    from app.utils.llm_client import get_llm_client
    bb = Blackboard()

    raw_log = bb.get(task_id, "rag_recall_log")
    if not raw_log:
        print("rag_recall_log not found.")
        return

    raw_sections = bb.get(task_id, "section_texts")
    section_texts = _j.loads(raw_sections) if isinstance(raw_sections, str) else (raw_sections or {})

    entries = _j.loads(raw_log) if isinstance(raw_log, str) else raw_log

    llm = get_llm_client()
    total_cited = 0
    total_items = 0

    print(f"=== RAG 引用率 ({len(entries)} subsections) ===\n")

    for e in entries[:5]:  # sample 5 sections
        sem_items = e.get("semantic_items", [])
        sec = e["section"]
        draft = section_texts.get(str(sec), "") or section_texts.get(sec, "")
        if not draft:
            continue
        draft_sample = draft[:1000]
        print(f"[{e['section']}.{e['subsection']}] query: {e['query'][:80]}")
        for i, item in enumerate(sem_items[:3], 1):  # top 3 per section
            total_items += 1
            text = item.get("text", "")
            if not text or len(text) < 20:
                continue
            prompt = f"""这篇文本是否引用了以下信息？只回答"引用"或"未引用"。

输出文本：{draft_sample}

召回信息：{text}

输出文本中是否包含上述信息的直接引用或改写？"""
            try:
                resp = llm.chat_completion(
                    [{"role": "user", "content": prompt}],
                    temperature=0, max_tokens=10, prompt_name="rag_cite"
                )
                cited = "引用" in resp
                if cited:
                    total_cited += 1
                print(f"  #{i} {'[引用]' if cited else '[未引用]'} {text[:80]}")
            except Exception as ex:
                print(f"  #{i} error: {ex}")
        print()

    print(f"=== 引用率 ===")
    print(f"引用: {total_cited}/{total_items} = {total_cited/max(total_items,1)*100:.1f}%")


def eval_style_stability(task_id: str) -> None:
    """对比初始风格和终稿风格，计算漂移量。需要 LLM API key。"""
    from app.blackboard import Blackboard
    from app.agents.style_analyzer import StyleAnalyzer

    bb = Blackboard()

    baseline_raw = bb.get(task_id, "style_baseline")
    draft = bb.get(task_id, "draft")

    if not baseline_raw:
        print("style_baseline 不存在。任务完成后会自动写入。")
        return
    if not draft:
        print("draft 不存在。")
        return

    baseline = json.loads(baseline_raw)
    if isinstance(baseline, str):
        baseline = json.loads(baseline)

    # 切终稿前 6000 字符做风格分析
    sample = draft[:6000] if len(draft) > 6000 else draft

    print("=== 正在用 StyleAnalyzer 重跑终稿... ===\n")
    sa = StyleAnalyzer()
    result = sa.analyze(sample)

    if not result:
        print("StyleAnalyzer 分析失败")
        return

    # 对比 core_fields 的漂移
    core_fields = [
        ("emotion_intensity", 50),
        ("short_sentence_ratio", 0.3),
        ("medium_sentence_ratio", 0.5),
        ("long_sentence_ratio", 0.2),
        ("adjective_density", 0.15),
        ("dialogue_ratio", 0.3),
        ("metaphor_frequency", "适度"),
    ]

    print("=== 风格稳定性对比 ===\n")
    print(f"{'字段':<25} {'初始':<12} {'终稿':<12} {'漂移':<10}")
    print("-" * 60)

    # 归一化句式占比（仅当三字段都存在时）
    sent_fields = ["short_sentence_ratio", "medium_sentence_ratio", "long_sentence_ratio"]
    sent_all_exist = all(f in baseline for f in sent_fields) and all(f in result for f in sent_fields)
    if sent_all_exist:
        orig_sent_sum = sum(baseline[f] for f in sent_fields) or 1
        curr_sent_sum = sum(result[f] for f in sent_fields) or 1

    drifts = []
    for field, default in core_fields:
        orig = baseline.get(field, default)
        curr = result.get(field, default)
        if isinstance(orig, (int, float)) and isinstance(curr, (int, float)):
            if sent_all_exist and field in sent_fields:
                orig = orig / orig_sent_sum
                curr = curr / curr_sent_sum
            drift = abs(orig - curr)
            pct = drift / max(abs(orig), 0.01) * 100
            drifts.append((field, pct))
            if pct > 15:
                print(f"{field:<25} {str(round(orig,2)):<12} {str(round(curr,2)):<12} {drift:.3f} ({pct:.0f}%) !!")
            else:
                print(f"{field:<25} {str(round(orig,2)):<12} {str(round(curr,2)):<12} {drift:.3f} ({pct:.0f}%)")
        else:
            print(f"{field:<25} {str(orig):<12} {str(curr):<12} (非数值，人工对比)")

    if drifts:
        avg_drift = sum(d for _, d in drifts) / len(drifts)
        print(f"\n平均漂移: {avg_drift:.1f}%")
        high = [f for f, d in drifts if d > 15]
        if high:
            print(f"[!] 不稳定字段: {', '.join(high)} (>15%漂移)")
            print("建议: 加强风格简报注入权重，或在 WRITER_SYSTEM_PROMPT 里强调风格一致性")
        else:
            print("[OK] 所有数值字段漂移 < 15%，风格稳定")


def eval_perf(task_id: str) -> None:
    """逐节延迟，计算 p50/p95。"""
    import json as _j
    from app.blackboard import Blackboard
    bb = Blackboard()

    raw = bb.get(task_id, "section_timings")
    if not raw:
        print("section_timings not found. Task must complete first.")
        return

    entries = _j.loads(raw) if isinstance(raw, str) else raw
    if not entries:
        print("No section timing data.")
        return

    latencies = sorted([t["total_time_s"] for t in entries])
    n = len(latencies)
    p50 = latencies[int(n * 0.5)] if n > 0 else 0
    p95 = latencies[min(int(n * 0.95), n - 1)] if n > 0 else 0
    avg_latency = sum(latencies) / n if n > 0 else 0

    llm_times = sorted([t["llm_time_s"] for t in entries])
    avg_llm = sum(llm_times) / n if n > 0 else 0

    print(f"=== End-to-End Latency ({n} subsections) ===")
    print(f"  avg: {avg_latency:.1f}s  p50: {p50:.1f}s  p95: {p95:.1f}s")
    print(f"  min: {latencies[0]:.1f}s  max: {latencies[-1]:.1f}s")
    print(f"=== LLM Time ===")
    print(f"  avg: {avg_llm:.1f}s  p50: {llm_times[int(n * 0.5)]:.1f}s  p95: {llm_times[min(int(n * 0.95), n - 1)]:.1f}s")
    print(f"=== Per-Section ===")
    print(f"  {'sec.sub':<10} {'LLM(s)':<10} {'total(s)':<12} {'chars':<8}")
    for t in entries:
        print(f"  [{t['section']}.{t['subsection']}]{'':<3} {t['llm_time_s']:<10} {t['total_time_s']:<12} {t['char_count']:<8}")


def _regex_style_stats(text: str) -> dict:
    """正则精确统计风格指标。"""
    import re
    total = max(len(text), 1)
    sentences = re.split(r'[。！？]', text)
    sentences = [s.strip() for s in sentences if s.strip()]
    sent_lens = [len(s) for s in sentences]
    n = max(len(sentences), 1)

    quotes = re.findall(r'["“「][^"”」]{2,}["”」]', text)
    dialogue_chars = sum(len(q) for q in quotes)

    paragraphs = re.split(r'\n{2,}', text)
    paragraphs = [p.strip() for p in paragraphs if p.strip()]
    np = max(len(paragraphs), 1)
    para_lens = [len(p) for p in paragraphs]

    return {
        "short_sentence_ratio": round(sum(1 for l in sent_lens if l < 15) / n, 3),
        "medium_sentence_ratio": round(sum(1 for l in sent_lens if 15 <= l <= 30) / n, 3),
        "long_sentence_ratio": round(sum(1 for l in sent_lens if l > 30) / n, 3),
        "dialogue_ratio": round(dialogue_chars / total, 3),
        "paragraph_length_avg": round(sum(para_lens) / np, 1),
        "paragraph_length_median": round(sorted(para_lens)[np // 2], 1),
        "exclamation_ratio": round(len(re.findall(r'！', text)) / total, 5),
        "n_sentences": n, "n_paragraphs": np,
    }


def _llm_style_judge(style_brief: str, draft: str) -> dict:
    """LLM 对风格简报逐项打分 (1-5)，不估数字。"""
    from app.utils.llm_client import get_llm_client
    from app.utils.json_parser import parse_json
    import re as _re
    llm = get_llm_client()
    prompt = f"""严苛打分：对照风格简报，评估终稿遵循度。注意——不要因为文字优美就给高分。找出实际偏离简报的地方才扣分。1=严重偏离，3=基本符合但有明显差距，5=精确遵循。

风格简报：{style_brief[:800]}

终稿（前2000字）：{draft[:2000]}

输出纯JSON（不要markdown代码块）：{{"emotion_tone":1-5,"sentence_rhythm":1-5,"dialogue_style":1-5,"rhetoric_restraint":1-5,"paragraph_craft":1-5,"overall_adherence":1-5,"brief_comment":"一句话指出最大偏离点"}}"""
    try:
        resp = llm.chat_completion([{"role":"user","content":prompt}], temperature=0, max_tokens=300, prompt_name="style_judge")
        # try parse_json first, then fall back to regex extraction
        try:
            return parse_json(resp)
        except Exception:
            # regex fallback: extract numbers after each key
            scores = {}
            for key in ["emotion_tone","sentence_rhythm","dialogue_style","rhetoric_restraint","paragraph_craft","overall_adherence"]:
                m = _re.search(rf'"{key}"\s*:\s*(\d)', resp)
                if m:
                    scores[key] = int(m.group(1))
            if scores:
                scores["brief_comment"] = "regex-extracted"
                return scores
            return {"error": "parse failed", "raw": resp[:200]}
    except Exception as e:
        return {"error": str(e)[:100]}


def eval_style_objective(task_id: str) -> None:
    """正则统计 + LLM 打分，双层风格评估。"""
    import json as _j
    from app.blackboard import Blackboard
    bb = Blackboard()

    baseline_raw = bb.get(task_id, "style_baseline")
    draft = bb.get(task_id, "draft")
    style_raw = bb.get(task_id, "style")

    if not draft:
        print("draft not found.")
        return

    # Part 1: 正则统计
    actual = _regex_style_stats(draft)
    print(f"=== Part 1: 正则统计 ({actual['n_sentences']}句 {actual['n_paragraphs']}段) ===\n")

    baseline = {}
    if baseline_raw:
        baseline = _j.loads(baseline_raw) if isinstance(baseline_raw, str) else baseline_raw

    # Part 1a: 纯正则统计（无基线对比）
    print(f"短句占比(<15字)     {actual['short_sentence_ratio']:.0%}")
    print(f"中句占比(15-30字)   {actual['medium_sentence_ratio']:.0%}")
    print(f"长句占比(>30字)     {actual['long_sentence_ratio']:.0%}")
    print(f"对话占比(引号内容)   {actual['dialogue_ratio']:.0%}")
    print(f"段长均值            {actual['paragraph_length_avg']:.0f}字 (中位{actual['paragraph_length_median']:.0f}字)")
    print(f"感叹号密度          {actual['exclamation_ratio']:.4f}")

    # Part 1b: 如果有 baseline，对比漂移
    if baseline:
        sent_fields = ["short_sentence_ratio", "medium_sentence_ratio", "long_sentence_ratio"]
        sent_all = all(f in baseline for f in sent_fields)
        print(f"\n--- LLM基线对比 ---")
        if sent_all:
            bsum = sum(baseline[f] for f in sent_fields) or 1
            asum = sum(actual[f] for f in sent_fields) or 1
            print(f"短句(基线) {baseline['short_sentence_ratio']/bsum:.0%} → (实测) {actual['short_sentence_ratio']/asum:.0%}")
            print(f"中句(基线) {baseline['medium_sentence_ratio']/bsum:.0%} → (实测) {actual['medium_sentence_ratio']/asum:.0%}")
            print(f"长句(基线) {baseline['long_sentence_ratio']/bsum:.0%} → (实测) {actual['long_sentence_ratio']/asum:.0%}")

    # Part 2: LLM judge

    # Part 2: LLM judge
    style_brief = ""
    if style_raw:
        style = _j.loads(style_raw) if isinstance(style_raw, str) else style_raw
        if isinstance(style, dict):
            style_brief = style.get("style_brief", "")
    if style_brief:
        print(f"\n=== Part 2: LLM 风格遵循度打分 ===\n")
        scores = _llm_style_judge(style_brief, draft)
        if "error" not in scores:
            for k, v in scores.items():
                if isinstance(v, (int, float)):
                    bar = "#" * v + "-" * (5 - v)
                    print(f"  {k:<20} {v}/5 {bar}")
                else:
                    print(f"  {k}: {v}")
            nums = [v for v in scores.values() if isinstance(v, (int, float))]
            if nums:
                print(f"\n  平均: {sum(nums)/len(nums):.1f}/5")
        else:
            print(f"  {scores['error']}")


def _load_rag_data(task_id: str, n_sample: int = 15, seed: int = 42):
    """加载 RAG 数据 + 章节上下文，返回采样后的 entries 和辅助结构。"""
    import json as _j
    import random
    from app.blackboard import Blackboard

    bb = Blackboard()

    raw_log = bb.get(task_id, "rag_recall_log")
    if not raw_log:
        return None, None, None, None

    entries = _j.loads(raw_log) if isinstance(raw_log, str) else raw_log
    candidates = [e for e in entries if e.get("semantic_items")]
    if not candidates:
        return None, None, None, None

    if len(candidates) > n_sample:
        random.seed(seed)
        sample = random.sample(candidates, n_sample)
    else:
        sample = candidates

    # 章节索引
    outline_raw = bb.get(task_id, "outline")
    outline = []
    if outline_raw:
        outline = _j.loads(outline_raw) if isinstance(outline_raw, str) else outline_raw

    section_index: dict[int, str] = {}
    if outline:
        for sec in outline:
            sn = sec.get("section", sec.get("section_num", 0))
            title = sec.get("title", "")
            if sn:
                section_index[int(sn)] = title

    raw_sections = bb.get(task_id, "section_texts")
    section_texts: dict[str, str] = {}
    if raw_sections:
        section_texts = _j.loads(raw_sections) if isinstance(raw_sections, str) else raw_sections

    # 章节摘要
    all_section_nums = sorted(set(
        list(section_index.keys()) +
        [int(k) for k in section_texts.keys() if str(k).isdigit()]
    ))
    sec_context_lines = []
    for sn in all_section_nums[:40]:
        title = section_index.get(sn, "")
        preview = ""
        txt = section_texts.get(str(sn), "") or section_texts.get(sn, "")
        if txt:
            preview = txt[:120].replace("\n", " ")
        line = f"第{sn}节"
        if title:
            line += f" · {title}"
        if preview:
            line += f": {preview}..."
        sec_context_lines.append(line)
    sec_context = "\n".join(sec_context_lines)

    return sample, section_texts, all_section_nums, sec_context


def eval_rag_export(task_id: str, n_sample: int = 15, k: int = 5) -> str:
    """导出人工标注模板 JSON 文件。"""
    import json as _j
    import os

    sample, section_texts, all_section_nums, sec_context = _load_rag_data(task_id, n_sample)
    if sample is None:
        print("无法加载 RAG 数据。")
        return ""

    export_entries = []
    for idx, e in enumerate(sample, 1):
        items = []
        for i, item in enumerate(e.get("semantic_items", [])[:k], 1):
            sec = item.get("section", "?")
            full_sec = section_texts.get(str(sec), "") or section_texts.get(sec, "")
            text_snippet = item.get("text", "")
            if full_sec and len(text_snippet) < 60:
                pos = full_sec.find(text_snippet[:30]) if text_snippet else -1
                if pos >= 0:
                    start = max(0, pos - 30)
                    end = min(len(full_sec), pos + 150)
                    text_snippet = full_sec[start:end]
            items.append({
                "item_index": i,
                "section": sec,
                "title": item.get("title", ""),
                "text": text_snippet,
                "human_relevant": None,  # 填 "相关" 或 "不相关"
            })

        export_entries.append({
            "query_index": idx,
            "section": e["section"],
            "subsection": e["subsection"],
            "query": e["query"],
            "items": items,
            "human_gt_sections": None,  # 填章节号列表如 [1,3,5]，或 [] 表示无
        })

    export = {
        "task_id": task_id,
        "seed": 42,
        "k": k,
        "n_queries": len(export_entries),
        "annotation_guide": {
            "human_relevant": "每条检索结果标记 '相关' 或 '不相关'——这段文字是否和查询涉及同一事件/人物/设定/情节点",
            "human_gt_sections": "你认为哪些已写章节应包含回答此查询所需信息？填章节号列表如 [1,3,5]，如无则填 []",
        },
        "entries": export_entries,
    }

    out_path = f"tests/rag_annotation_{task_id[:8]}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        _j.dump(export, f, ensure_ascii=False, indent=2)
    print(f"标注模板已导出: {out_path}")
    print(f"共 {len(export_entries)} 个查询，每查询最多 {k} 条检索结果")
    print(f"\n标注方法:")
    print(f"  1. 阅读每个 query，理解该小节写作时需要的背景信息")
    print(f"  2. 对每条 items[i]，判断 item['text'] 是否与查询需求相关")
    print(f"  3. 将 human_relevant 改为 '相关' 或 '不相关'")
    print(f"  4. (可选) 阅读下方章节摘要，将 human_gt_sections 改为你认为相关的章节号列表")
    print(f"\n章节摘要供参考:\n{sec_context[:2000]}...")
    return out_path


def eval_rag_compare(task_id: str, annotation_file: str) -> None:
    """对比 LLM judge 与人工标注，计算一致性。"""
    import json as _j
    import re as _re
    from app.utils.llm_client import get_llm_client

    with open(annotation_file, "r", encoding="utf-8") as f:
        ann = _j.load(f)

    ann_entries = ann["entries"]
    k = ann.get("k", 5)

    # 加载章节数据（失败时降级：只用标注文件中的数据，不影响对比）
    loaded = _load_rag_data(task_id, n_sample=len(ann_entries), seed=ann.get("seed", 42))
    if loaded[0] is not None:
        _, section_texts, all_section_nums, sec_context = loaded
    else:
        section_texts, all_section_nums, sec_context = {}, [], ""

    llm = get_llm_client()

    # 混淆矩阵
    tp = fp = tn = fn = 0  # human=relevant as ground truth
    total_compared = 0

    print(f"=== LLM Judge vs 人工标注一致性 ===\n")
    print(f"标注文件: {annotation_file}")
    print(f"查询数: {len(ann_entries)}\n")

    for entry in ann_entries:
        query = entry["query"]
        items = entry.get("items", [])
        cur_section = entry["section"]

        if not items:
            continue

        human_labels = {}
        for item in items:
            label = item.get("human_relevant")
            if label and label in ("相关", "不相关"):
                human_labels[item["item_index"]] = (label == "相关")

        if not human_labels:
            print(f"  [{entry['query_index']}] 跳过: 无人工标注")
            continue

        # --- LLM Precision 判断 ---
        items_text_parts = []
        for item in items:
            idx = item["item_index"]
            sec = item.get("section", "?")
            title = item.get("title", "")
            text_snippet = item.get("text", "")
            full_sec = section_texts.get(str(sec), "") or section_texts.get(sec, "")
            if full_sec and len(text_snippet) < 60:
                pos = full_sec.find(text_snippet[:30]) if text_snippet else -1
                if pos >= 0:
                    start = max(0, pos - 30)
                    end = min(len(full_sec), pos + 150)
                    text_snippet = full_sec[start:end]
            sec_info = f"第{sec}节"
            if title:
                sec_info += f" · {title}"
            items_text_parts.append(f"[{idx}] {sec_info}\n    \"{text_snippet}\"")
        items_text = "\n".join(items_text_parts)

        precision_prompt = f"""你是一个严格的RAG评估器。给定一个写作查询和检索到的文本片段，判断每个片段是否与查询**主题相关**（内容涉及同一事件、同一人物、同一设定或同一情节点）。

查询：{query}

检索到的片段：
{items_text}

对每个编号的片段，判断是否相关。只输出判定结果，格式严格为：
[1] 相关
[2] 不相关
[3] 相关
...
不要输出其他内容。"""

        llm_labels = {}
        try:
            resp = llm.chat_completion(
                [{"role": "user", "content": precision_prompt}],
                temperature=0, max_tokens=120, prompt_name="rag_precision"
            )
            for item in items:
                i = item["item_index"]
                m = _re.search(rf'\[{i}\]\s*(相关|不相关)', resp)
                if m:
                    llm_labels[i] = (m.group(1) == "相关")
        except Exception as ex:
            print(f"  [{entry['query_index']}] LLM error: {ex}")
            continue

        # 逐条对比
        for item in items:
            i = item["item_index"]
            h = human_labels.get(i)
            l = llm_labels.get(i)
            if h is None or l is None:
                continue
            total_compared += 1
            if h and l:
                tp += 1
            elif h and not l:
                fn += 1  # LLM missed a relevant item
            elif not h and l:
                fp += 1  # LLM hallucinated relevance
            else:
                tn += 1

    if total_compared == 0:
        print("没有可对比的标注数据。请先填写 human_relevant 字段。")
        return

    # 指标
    accuracy = (tp + tn) / total_compared * 100
    precision = tp / max(tp + fp, 1) * 100
    recall = tp / max(tp + fn, 1) * 100
    f1 = 2 * precision * recall / max(precision + recall, 0.01)

    # Cohen's Kappa
    po = (tp + tn) / total_compared
    pe_pos = ((tp + fp) / total_compared) * ((tp + fn) / total_compared)
    pe_neg = ((tn + fn) / total_compared) * ((tn + fp) / total_compared)
    pe = pe_pos + pe_neg
    kappa = (po - pe) / (1 - pe) if pe < 1 else 0

    print(f"{'='*50}")
    print(f"对比条数: {total_compared}")
    print(f"准确率 (Accuracy):    {tp+tn}/{total_compared} = {accuracy:.1f}%")
    print(f"  → LLM judge 与人类标注一致的占比")
    print()
    print(f"混淆矩阵 (人类标注 = Ground Truth):")
    print(f"                     LLM: 相关    LLM: 不相关")
    print(f"  人类: 相关          TP={tp:<7}  FN={fn}")
    print(f"  人类: 不相关        FP={fp:<7}  TN={tn}")
    print()
    print(f"Cohen's Kappa:        {kappa:.3f}")
    kappa_verdict = (
        "几乎完美一致" if kappa > 0.8 else
        "高度一致" if kappa > 0.6 else
        "中等一致" if kappa > 0.4 else
        "低度一致" if kappa > 0.2 else
        "几乎不一致"
    )
    print(f"  → {kappa_verdict}")
    print()
    print(f"LLM Precision (vs人类): {precision:.1f}%")
    print(f"LLM Recall (vs人类):    {recall:.1f}%")
    print(f"LLM F1 (vs人类):        {f1:.1f}%")
    print()
    if fn > 0:
        print(f"[!] LLM 漏判 {fn} 条 (人类认为相关但 LLM 判不相关)")
    if fp > 0:
        print(f"[!] LLM 误判 {fp} 条 (人类认为不相关但 LLM 判相关)")


def eval_rag_auto(task_id: str, n_sample: int = 15, k: int = 5) -> None:
    """LLM 自动评判 RAG 召回质量：Precision@K + 近似 Recall@K。

    Precision@K: LLM 逐条判断 top-K 检索结果是否与查询相关。
    Recall@K (近似): LLM 根据大纲/章节摘要标注 ground-truth 相关章节，
                     检查其中多少出现在检索结果中。
    """
    import json as _j
    import re as _re
    from app.utils.llm_client import get_llm_client

    sample, section_texts, all_section_nums, sec_context = _load_rag_data(task_id, n_sample)
    if sample is None:
        print("无法加载 RAG 数据。")
        return

    # ── LLM judge ──
    llm = get_llm_client()

    total_precision_items = 0
    total_precision_relevant = 0
    total_recall_gt = 0
    total_recall_hit = 0
    per_query_results: list[dict] = []

    print(f"=== RAG 自动评估: {len(sample)} 个查询 (K={k}) ===\n")

    for idx, e in enumerate(sample, 1):
        query = e["query"]
        sem_items = e.get("semantic_items", [])[:k]
        sem_sections = set(int(s) for s in e.get("semantic_sections", []) if s)

        if not sem_items:
            continue

        # --- Precision@K: 逐条判断相关性 ---
        items_text_parts = []
        for i, item in enumerate(sem_items, 1):
            sec = item.get("section", "?")
            title = item.get("title", "")
            text_snippet = item.get("text", "")
            # 尝试从 section_texts 扩展上下文
            full_sec = section_texts.get(str(sec), "") or section_texts.get(sec, "")
            if full_sec and len(text_snippet) < 60:
                # 在完整章节文本中定位该片段，取周围更长的上下文
                idx_in_sec = full_sec.find(text_snippet[:30]) if text_snippet else -1
                if idx_in_sec >= 0:
                    start = max(0, idx_in_sec - 30)
                    end = min(len(full_sec), idx_in_sec + 150)
                    text_snippet = full_sec[start:end]
            sec_info = f"第{sec}节"
            if title:
                sec_info += f" · {title}"
            items_text_parts.append(f"[{i}] {sec_info}\n    \"{text_snippet}\"")

        items_text = "\n".join(items_text_parts)

        precision_prompt = f"""你是一个严格的RAG评估器。给定一个写作查询和检索到的文本片段，判断每个片段是否与查询**主题相关**（内容涉及同一事件、同一人物、同一设定或同一情节点）。

查询：{query}

检索到的片段：
{items_text}

对每个编号的片段，判断是否相关。只输出判定结果，格式严格为：
[1] 相关
[2] 不相关
[3] 相关
...
不要输出其他内容。"""

        precision_results: dict[int, bool] = {}
        try:
            resp = llm.chat_completion(
                [{"role": "user", "content": precision_prompt}],
                temperature=0, max_tokens=120, prompt_name="rag_precision"
            )
            for i in range(1, len(sem_items) + 1):
                m = _re.search(rf'\[{i}\]\s*(相关|不相关)', resp)
                if m:
                    precision_results[i] = (m.group(1) == "相关")
                    total_precision_items += 1
                    if precision_results[i]:
                        total_precision_relevant += 1
        except Exception as ex:
            print(f"  [{idx}] Precision judge 出错: {ex}")
            continue

        # --- Recall@K (近似): LLM 标注 ground-truth 相关章节 ---
        # 只考虑当前节之前已写的章节（未来章节尚未存在，不可能被检索）
        past_section_nums = [sn for sn in all_section_nums if sn <= e["section"]]

        recall_prompt = f"""查询写作时需要的信息：{query}

当前正在写第{e['section']}节。只能从第1-{e['section']}节检索（后续章节尚未写出）。

以下是已写章节的内容摘要：
{sec_context[:3000]}

请列出**已写章节中与此查询最相关的3-5个章节**（不超过5个，且必须≤第{e['section']}节）。只输出章节号，用逗号分隔。
例如：1,3,5
如果没有明确相关章节，输出：无"""

        gt_sections: set[int] = set()
        try:
            resp2 = llm.chat_completion(
                [{"role": "user", "content": recall_prompt}],
                temperature=0, max_tokens=100, prompt_name="rag_recall_gt"
            )
            if "无" not in resp2:
                for num in _re.findall(r'\d+', resp2):
                    sn = int(num)
                    if sn in all_section_nums and sn <= e["section"]:
                        gt_sections.add(sn)

            if gt_sections:
                total_recall_gt += len(gt_sections)
                hits = gt_sections & sem_sections
                total_recall_hit += len(hits)
        except Exception as ex:
            print(f"  [{idx}] Recall judge 出错: {ex}")

        # 记录单条结果
        prec_k = sum(1 for v in precision_results.values() if v)
        per_query_results.append({
            "section": e["section"], "subsection": e["subsection"],
            "precision_hits": prec_k, "precision_total": len(precision_results),
            "gt_sections": gt_sections, "hit_sections": gt_sections & sem_sections,
        })

        if idx % 5 == 0:
            print(f"  ... {idx}/{len(sample)}")

    # ── 任务结构分析 ──
    n_sections = len(all_section_nums)
    task_type = "跨章节长篇" if n_sections >= 5 else ("少章节中篇" if n_sections >= 2 else "单节短篇")
    print(f"任务结构: {n_sections} 节 ({task_type})")
    if n_sections <= 1:
        print("[!] 单节任务: 所有检索均为节内检索，Recall 指标不适用。")
        print("   建议对多节长篇任务评估 Recall。")
    print()

    # ── 汇总 ──
    print(f"{'='*60}")
    print(f"=== RAG 召回率评估结果 ===")
    print(f"{'='*60}\n")
    print(f"样本数:  {len(sample)} 查询")
    print(f"K值:     Top-{k}")
    print()

    prec = total_precision_relevant / max(total_precision_items, 1) * 100
    recall = total_recall_hit / max(total_recall_gt, 1) * 100
    f1 = 2 * prec * recall / max(prec + recall, 0.01)

    print(f" Precision@{k}:  {total_precision_relevant}/{total_precision_items} = {prec:.1f}%")
    print(f"   → 检索结果中 {prec:.0f}% 被 LLM 判定为与查询相关")
    print()
    print(f" Recall@{k} (近似): {total_recall_hit}/{total_recall_gt} = {recall:.1f}%")
    print(f"   → LLM 标注的 ground-truth 相关章节中 {recall:.0f}% 被检索命中")
    print(f"   (注: ground-truth 由 LLM 根据章节摘要自动标注，非人工标注)")
    print()
    print(f" F1 Score:  {f1:.1f}%")
    print()

    # 逐条明细
    print(f"--- 逐查询明细 ---")
    print(f"  {'节.小节':<10} {'Prec':<8} {'GT章节':<15} {'命中':<10}")
    for r in per_query_results:
        sec_sub = f"{r['section']}.{r['subsection']}"
        prec_str = f"{r['precision_hits']}/{r['precision_total']}"
        gt_str = str(sorted(r['gt_sections'])) if r['gt_sections'] else "LLM判定无"
        hit_str = str(sorted(r['hit_sections'])) if r['hit_sections'] else "—"
        print(f"  {sec_sub:<10} {prec_str:<8} {gt_str:<15} {hit_str:<10}")

    print()
    print(f"评估方式: LLM-as-Judge (temperature=0)")
    print(f"如需人工标注对比: uv run python tests/eval_quality.py rag {task_id}")


def eval_style_drift(task_id: str) -> None:
    """对比基线风格 vs 终稿正则统计，量化执行漂移（4 维标签 → 预期区间）。"""
    import json as _j
    from app.blackboard import Blackboard
    bb = Blackboard()

    baseline_raw = bb.get(task_id, "style_baseline")
    draft = bb.get(task_id, "draft")

    if not baseline_raw:
        print("style_baseline 不存在。")
        return
    if not draft:
        print("draft 不存在。")
        return

    baseline = _j.loads(baseline_raw) if isinstance(baseline_raw, str) else baseline_raw
    actual = _regex_style_stats(draft)

    # 句长标签 → 预期区间 (短/中/长百分比)
    SENTENCE_EXPECTED = {
        "short":    {"short": (45, 60), "medium": (25, 35), "long": (10, 20)},
        "balanced": {"short": (25, 35), "medium": (30, 40), "long": (25, 35)},
        "long":     {"short": (10, 20), "medium": (25, 35), "long": (45, 60)},
    }
    sent_pref = baseline.get("sentence_preference", "balanced")
    expected = SENTENCE_EXPECTED.get(sent_pref, SENTENCE_EXPECTED["balanced"])

    # 归一化实际句长分布
    sent_fields = ["short_sentence_ratio", "medium_sentence_ratio", "long_sentence_ratio"]
    asum = sum(actual[f] for f in sent_fields) or 1

    print(f"=== 风格执行漂移 (4 维标签 → 预期区间) ===\n")
    print(f"句长偏好: {sent_pref}")
    print(f"{'维度':<20} {'预期区间':<18} {'终稿':<10} {'判定':<6}")
    print("-" * 58)

    alerts = []
    label_map = [("short_sentence_ratio", "short", "短句(<15字)"),
                 ("medium_sentence_ratio", "medium", "中句(15-30)"),
                 ("long_sentence_ratio", "long", "长句(>30字)")]
    for field, key, label in label_map:
        lo, hi = expected[key]
        a = actual[field] / asum * 100
        if lo <= a <= hi:
            alert = "[OK]"
        else:
            alert = "[!]"
            alerts.append(f"{label}: 预期{lo}-{hi}%, 实际{a:.0f}%")
        print(f"{label:<20} {f'{lo}%-{hi}%':<18} {a:5.1f}%   {alert}")

    # 对话占比
    dia_target = baseline.get("dialogue_ratio", 0.3)
    dia_actual = actual["dialogue_ratio"]
    dia_label = ""
    if dia_target >= 0.3:
        # 高对话期望：实际不能低于一半
        dia_ok = dia_actual >= dia_target * 0.4
        dia_label = f"预期>{dia_target*0.4:.0%}"
    else:
        # 低对话期望：在合理范围
        dia_ok = abs(dia_actual - dia_target) <= 0.08
        dia_label = f"预期≈{dia_target:.0%}"
    dia_alert = "[OK]" if dia_ok else "[!]"
    if not dia_ok:
        alerts.append(f"对话占比: {dia_label}, 实际{dia_actual:.0%}")
    print(f"{'对话占比':<20} {dia_label:<18} {dia_actual:5.1%}   {dia_alert}")

    # 情感强度 — 正则测不了，用 LLM judge 打分
    print(f"\n--- LLM 主观评分 ---")
    ei = baseline.get("emotion_intensity", 50)
    sd = baseline.get("sensory_density", "medium")
    judge = _llm_style_judge_4d(draft, ei, sd)
    for key, label in [("emotion_intensity", "情感强度"), ("sensory_density", "感官密度")]:
        score = judge.get(key, "-")
        print(f"{label:<20} 基线:{baseline.get(key)} | LLM评分: {score}")

    if alerts:
        print(f"\n不稳定维度 ({len(alerts)}):")
        for a in alerts:
            print(f"  - {a}")


def _llm_style_judge_4d(draft: str, emotion_intensity: int, sensory_density: str) -> dict:
    """LLM 对情感强度和感官密度主观评分 (1-5)。"""
    from app.utils.llm_client import get_llm_client
    from app.utils.json_parser import parse_json
    sd_label = {"sparse": "留白简洁", "medium": "适度描写", "rich": "五感丰富"}.get(sensory_density, "适度")
    prompt = f"""评分：对照风格目标对以下文本的两个维度打分 1-5。

情感强度目标：{emotion_intensity}/100 ({'克制' if emotion_intensity<=30 else '温婉' if emotion_intensity<=50 else '浓郁' if emotion_intensity<=70 else '激烈'})
感官密度目标：{sensory_density} ({sd_label})

文本（前2000字）：
{draft[:2000]}

输出 JSON：{{"emotion_intensity": 1-5, "sensory_density": 1-5}}（1=严重偏离，3=基本符合，5=精确遵循）"""
    try:
        llm = get_llm_client()
        resp = llm.chat_completion([{"role": "user", "content": prompt}], temperature=0, max_tokens=100, prompt_name="style_judge_4d")
        return parse_json(resp)
    except Exception:
        return {"emotion_intensity": "?", "sensory_density": "?"}


def eval_info_density(task_id: str) -> None:
    """逐节叙事密度量化：对话占比、事件数、段落数、句长。"""
    import json as _j
    from app.blackboard import Blackboard
    bb = Blackboard()

    raw = bb.get(task_id, "section_texts")
    if not raw:
        print("section_texts 不存在。")
        return
    st = _j.loads(raw) if isinstance(raw, str) else raw

    # 加载大纲获取节标题
    outline_raw = bb.get(task_id, "outline")
    titles = {}
    if outline_raw:
        out = _j.loads(outline_raw) if isinstance(outline_raw, str) else outline_raw
        for sec in out:
            titles[sec.get("section", 0)] = sec.get("title", "")

    print(f"=== 逐节叙事密度 ({len(st)} 节) ===\n")
    print(f"{'节':<5} {'标题':<20} {'字数':<8} {'对话%':<8} {'段落':<6} {'句数':<6} {'感叹号':<8}")
    print("-" * 75)

    total_chars = 0
    total_dialogue = 0
    total_paras = 0
    total_sents = 0

    for k in sorted(st.keys(), key=lambda x: int(x) if str(x).isdigit() else 0):
        text = st[k]
        stats = _regex_style_stats(text)
        sec_num = int(k) if str(k).isdigit() else k
        title = titles.get(sec_num, "")[:18]
        chars = len(text)
        total_chars += chars
        total_dialogue += int(stats["dialogue_ratio"] * chars)
        total_paras += stats["n_paragraphs"]
        total_sents += stats["n_sentences"]

        # 密度判定
        dia_pct = stats["dialogue_ratio"]
        dia_flag = " !高" if dia_pct > 0.4 else (" 低" if dia_pct < 0.05 else "")
        print(f"{sec_num:<5} {title:<20} {chars:<8} {dia_pct:5.0%}{dia_flag:<4} "
              f"{stats['n_paragraphs']:<6} {stats['n_sentences']:<6} "
              f"{stats['exclamation_ratio']:.4f}")

    print(f"\n--- 汇总 ---")
    n = max(len(st), 1)
    print(f"总字数: {total_chars}  平均对话占比: {total_dialogue/max(total_chars,1):.0%}")
    print(f"总段落: {total_paras} ({total_paras/n:.0f}/节)  总句数: {total_sents} ({total_sents/n:.0f}/节)")
    print(f"平均句长: {total_chars/max(total_sents,1):.0f} 字")


def eval_contradiction(task_id: str) -> None:
    """展示矛盾检测统计：检出量 / 严重比例 / 执行率。"""
    import json as _j
    from app.blackboard import Blackboard
    bb = Blackboard()

    raw = bb.get(task_id, "contradiction_stats")
    if not raw:
        print("contradiction_stats 不存在。需要运行 v0.9.4+ 版本的任务。")
        print("旧任务可以查看 fix_checklist 获取部分信息。")
        fix_raw = bb.get(task_id, "fix_checklist")
        if fix_raw:
            fix = _j.loads(fix_raw) if isinstance(fix_raw, str) else fix_raw
            print(f"\n=== 旧格式 fix_checklist ===")
            print(f"严重修正: {len(fix.get('critical_fixes', []))} 条")
            print(f"轻微修正: {len(fix.get('minor_fixes', []))} 条")
            for c in fix.get("critical_fixes", [])[:5]:
                print(f"  - 第{c.get('target_section', '?')}节: {c.get('description', '')[:120]}")
        return

    stats = _j.loads(raw) if isinstance(raw, str) else raw

    total = stats.get("total_backrefs", 0)
    critical = stats.get("critical_fixes", 0)
    minor = stats.get("minor_fixes", 0)
    applied = stats.get("fixes_applied", 0)
    skipped = stats.get("fixes_skipped", 0)
    sec_count = stats.get("sections_with_backrefs", 0)

    print(f"=== 矛盾检测统计 (ContinuityEditor) ===\n")
    print(f"Writer 回溯建议:        {total} 条")
    print(f"涉及章节:              {sec_count} 节")
    print(f"")
    print(f"LLM 分级判断:")
    print(f"  严重 (critical):     {critical} 条 ({critical/max(total,1)*100:.0f}%)")
    print(f"  轻微 (minor):        {minor} 条 ({minor/max(total,1)*100:.0f}%)")
    print(f"")
    print(f"修正执行:")
    print(f"  已执行:              {applied} 条")
    print(f"  跳过 (无匹配章节):    {skipped} 条")
    print(f"  执行率:              {applied/max(critical,1)*100:.0f}%")
    print(f"")
    print(f"--- 解读 ---")
    if total == 0:
        print(f"本次写作未产生任何回溯修正建议。")
        print(f"可能原因: 故事线简单、Writer 未启用回溯检测、或确实没有矛盾。")
    else:
        severity_rate = critical / max(total, 1) * 100
        if severity_rate > 50:
            print(f"严重比例 {severity_rate:.0f}%: Writer 的多数回溯建议被判定为需修正。")
            print(f"建议检查 Writer 是否过于敏感，或在 prompt 中降低回溯建议的激进程度。")
        else:
            print(f"严重比例 {severity_rate:.0f}%: ContinuityEditor 过滤掉了大部分噪音。")

    print(f"\n注: 假阳性率需人工审阅 fix_checklist 中的每项判断。")
    print(f"   查看原始数据: bb.get('{task_id}', 'fix_checklist')")


def eval_style_reproducibility(task_id: str, n_runs: int = 3) -> None:
    """风格分析可复现性：同一文本跑 N 次，计算 profile 余弦相似度 + 分类字段一致率。"""
    import json as _j
    import math
    from app.blackboard import Blackboard
    from app.agents.style_analyzer import StyleAnalyzer

    bb = Blackboard()

    ref_text = bb.get(task_id, "reference_text")
    if not ref_text:
        # fallback: use first section of section_texts
        raw = bb.get(task_id, "section_texts")
        if raw:
            st = _j.loads(raw) if isinstance(raw, str) else raw
            first = st.get("1", "") or st.get(1, "") or next(iter(st.values()), "")
            ref_text = first[:6000]
    if not ref_text:
        print("找不到参考文本。需要有 reference_text 或 section_texts。")
        return

    ref_sample = ref_text[:6000]
    print(f"=== 风格可复现性: {n_runs} 次运行 ===\n")
    print(f"输入文本: {len(ref_sample)} 字符")
    print(f"(temperature=0.3, 每次都重新调用 LLM)\n")

    sa = StyleAnalyzer()
    profiles = []

    for i in range(n_runs):
        print(f"  第 {i+1}/{n_runs} 次分析...")
        profile = sa.analyze(ref_sample)
        if not profile:
            print(f"    失败，跳过")
            continue
        profiles.append(profile)
        print(f"    emotion_intensity={profile.get('emotion_intensity','?')}, "
              f"sentence={profile.get('sentence_preference','?')}, "
              f"dialogue={profile.get('dialogue_ratio','?')}, "
              f"sensory={profile.get('sensory_density','?')}")

    if len(profiles) < 2:
        print("有效运行次数不足，无法计算相似度。")
        return

    # ── 数值字段余弦相似度 ──
    numeric_keys = [k for k, v in profiles[0].items() if isinstance(v, (int, float))]
    vectors = []
    for p in profiles:
        vec = [p.get(k, 0) for k in numeric_keys]
        vectors.append(vec)

    def cosine(a, b):
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(y * y for y in b))
        return dot / (na * nb) if na > 0 and nb > 0 else 0

    pairs = [(i, j) for i in range(len(profiles)) for j in range(i + 1, len(profiles))]
    cosines = [cosine(vectors[i], vectors[j]) for i, j in pairs]
    avg_cosine = sum(cosines) / len(cosines) if cosines else 0

    # ── 离散字段一致率 ──
    categorical_keys = [k for k, v in profiles[0].items() if isinstance(v, str)]
    cat_matches = 0
    cat_total = 0
    for i, j in pairs:
        for k in categorical_keys:
            cat_total += 1
            if profiles[i].get(k) == profiles[j].get(k):
                cat_matches += 1
    cat_agreement = cat_matches / max(cat_total, 1) * 100

    # ── 输出 ──
    print(f"\n{'='*50}")
    print(f"=== 可复现性结果 ===\n")

    print(f"数值字段 ({len(numeric_keys)} 维) 余弦相似度:")
    for (i, j), c in zip(pairs, cosines):
        print(f"  运行{i+1} vs 运行{j+1}: {c:.4f}")
    print(f"  平均: {avg_cosine:.4f}")
    verdict_num = "优秀" if avg_cosine > 0.95 else ("良好" if avg_cosine > 0.85 else ("一般" if avg_cosine > 0.7 else "差"))
    print(f"  评级: {verdict_num}")

    print(f"\n离散字段 ({len(categorical_keys)} 维) 一致率:")
    print(f"  完全一致: {cat_matches}/{cat_total} = {cat_agreement:.1f}%")

    # 数值字段漂移明细
    print(f"\n--- 数值字段稳定性 (CV%) ---")
    for k in numeric_keys:
        vals = [p.get(k, 0) for p in profiles]
        mean_v = sum(vals) / len(vals)
        if mean_v > 0.001:
            std_v = math.sqrt(sum((v - mean_v) ** 2 for v in vals) / len(vals))
            cv = std_v / mean_v * 100
            if cv > 20:
                print(f"  {k}: CV={cv:.0f}%  (不稳定)")
            elif cv > 10:
                print(f"  {k}: CV={cv:.0f}%")

    print(f"\n结论: 数值字段余弦 {avg_cosine:.3f}, 离散字段一致 {cat_agreement:.0f}%")
    if avg_cosine >= 0.90 and cat_agreement >= 80:
        print("风格分析具有良好可复现性，可以放心使用。")
    elif avg_cosine >= 0.75:
        print("风格分析中等可复现性。建议在 prompt 中降低 temperature 或在系统提示中强调稳定性。")
    else:
        print("风格分析可复现性较差。同一文本跑多次可能得到明显不同的风格参数。")


def eval_token_cost(task_id: str) -> None:
    """展示 Agent token 分账 & 成本。"""
    import json as _j
    from app.blackboard import Blackboard
    bb = Blackboard()

    raw = bb.get(task_id, "token_cost")
    if not raw:
        # 兼容旧任务: 只有 token_usage
        raw_usage = bb.get(task_id, "token_usage")
        if raw_usage:
            total = int(raw_usage) if isinstance(raw_usage, str) else raw_usage
            print(f"=== Token 用量 (旧格式) ===")
            print(f"总 Token: {total}")
            print(f"预估成本: ${total * 0.000000435:.4f}")
        else:
            print("token_cost 不存在。任务完成后会自动写入。")
        return

    tc = _j.loads(raw) if isinstance(raw, str) else raw

    print(f"=== Token 成本分析 ===\n")
    print(f"总 Token:    {tc.get('total_tokens', 0):,}")
    print(f"总耗时:      {tc.get('total_time_s', 0):.1f}s")
    print(f"预估成本:    ${tc.get('est_cost_usd', 0):.4f}")
    print()

    by_agent = tc.get("by_agent", {})
    if by_agent:
        total = tc.get("total_tokens", 1)
        print(f"--- Agent Token 分布 ---")
        print(f"  {'Agent':<20} {'Token':<12} {'占比':<8}")
        sorted_agents = sorted(by_agent.items(), key=lambda x: x[1], reverse=True)
        for agent, tokens in sorted_agents:
            pct = tokens / max(total, 1) * 100
            bar = "#" * int(pct / 5) + "-" * (20 - int(pct / 5))
            print(f"  {agent:<20} {tokens:<12,} {pct:5.1f}% {bar}")
        print()
        print(f"最烧钱 Agent: {sorted_agents[0][0]} ({sorted_agents[0][1]/max(total,1)*100:.0f}%)")
    else:
        print("无 Agent 分账数据。需要运行 v0.9.4+ 版本的任务。")


def main():
    if len(sys.argv) < 3:
        print("Usage: eval_quality.py <rag|rag-auto|rag-cite|style|style-real|perf|contradiction|style-repro|token-cost> <task_id> [n_sample]")
        sys.exit(1)

    mode = sys.argv[1]
    task_id = sys.argv[2]

    if mode == "rag":
        eval_rag_recall(task_id)
    elif mode == "rag-auto":
        n_sample = int(sys.argv[3]) if len(sys.argv) > 3 else 15
        eval_rag_auto(task_id, n_sample=n_sample)
    elif mode == "rag-export":
        n_sample = int(sys.argv[3]) if len(sys.argv) > 3 else 15
        eval_rag_export(task_id, n_sample=n_sample)
    elif mode == "rag-cmp":
        anno_file = sys.argv[3] if len(sys.argv) > 3 else ""
        if not anno_file:
            print("Usage: eval_quality.py rag-cmp <task_id> <annotation_file.json>")
            sys.exit(1)
        eval_rag_compare(task_id, anno_file)
    elif mode == "rag-cite":
        eval_rag_cite(task_id)
    elif mode == "style":
        eval_style_stability(task_id)
    elif mode == "style-real":
        eval_style_objective(task_id)
    elif mode == "perf":
        eval_perf(task_id)
    elif mode == "contradiction":
        eval_contradiction(task_id)
    elif mode == "style-repro":
        n = int(sys.argv[3]) if len(sys.argv) > 3 else 3
        eval_style_reproducibility(task_id, n_runs=n)
    elif mode == "token-cost":
        eval_token_cost(task_id)
    elif mode == "style-drift":
        eval_style_drift(task_id)
    elif mode == "density":
        eval_info_density(task_id)
    else:
        print(f"Unknown mode: {mode}")


if __name__ == "__main__":
    main()
