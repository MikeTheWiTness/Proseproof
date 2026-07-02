import re


START_MARKER = r"(\\?#){6}\s*片段开始\s*(\\?#){6}"
END_MARKER = r"(\\?#){6}\s*片段结束\s*(\\?#){6}"

START_KNOWLEDGE_MARKER = r"(\\?#){6}\s*知识开始\s*(\\?#){6}"
END_KNOWLEDGE_MARKER = r"(\\?#){6}\s*知识结束\s*(\\?#){6}"


class ManualMarkerError(ValueError):
    pass


class KnowledgeMarkerError(ValueError):
    pass


def split_by_manual_markers(md_content):
    return _split_by_markers(md_content, "片段", START_MARKER, END_MARKER, ManualMarkerError)


def split_by_knowledge_markers(md_content):
    return _split_by_markers(md_content, "知识", START_KNOWLEDGE_MARKER, END_KNOWLEDGE_MARKER, KnowledgeMarkerError)


def _split_by_markers(md_content, unit_label, start_pattern, end_pattern, error_cls):
    lines = md_content.splitlines()
    problems = []
    current_content = []
    in_problem = False
    start_count = 0
    end_count = 0

    for i, line in enumerate(lines, start=1):
        stripped = line.strip()
        is_start = bool(re.match(f"^{start_pattern}$", stripped))
        is_end = bool(re.match(f"^{end_pattern}$", stripped))

        if is_start:
            start_count += 1
            if in_problem:
                raise error_cls(
                    f"第 {i} 行：发现未闭合的{unit_label}开始标记，标记不配对"
                )
            in_problem = True
            current_content = []
        elif is_end:
            end_count += 1
            if not in_problem:
                raise error_cls(
                    f"第 {i} 行：发现没有对应开始标记的{unit_label}结束标记，标记不配对"
                )
            problems.append({"content": "\n".join(current_content)})
            in_problem = False
        else:
            if in_problem:
                current_content.append(line)

    if start_count == 0 and end_count == 0:
        raise error_cls(
            f"未找到任何{unit_label}标记（###### {unit_label}开始 ###### / ###### {unit_label}结束 ######），"
            "请在文档中添加成对标记"
        )

    if in_problem:
        raise error_cls(
            f"标记不配对：找到 {start_count} 个开始标记，{end_count} 个结束标记，"
            f"最后一个{unit_label}缺少结束标记"
        )

    if start_count != end_count:
        raise error_cls(
            f"标记不配对：找到 {start_count} 个开始标记，{end_count} 个结束标记"
        )

    return problems
