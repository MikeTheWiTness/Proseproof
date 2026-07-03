import os, re, json, logging

_log = logging.getLogger(__name__)

_config_cache = {}


def clear_config_cache():
    _config_cache.clear()


def load_config(profile_dir):
    cache_key = profile_dir
    cached = _config_cache.get(cache_key)
    if cached is not None:
        return cached

    config_file = os.path.join(profile_dir, "config.json")
    if not os.path.exists(config_file):
        raise FileNotFoundError(f"配置文件不存在: {config_file}")

    with open(config_file, 'r', encoding='utf-8') as f:
        new_data = json.load(f)

    if "question_prompt_lines" not in new_data:
        raise ValueError(f"配置文件缺少 question_prompt_lines: {config_file}")
    if "knowledge_prompt_lines" not in new_data:
        raise ValueError(f"配置文件缺少 knowledge_prompt_lines: {config_file}")

    config = {}
    config["question_prompt_lines"] = new_data["question_prompt_lines"]
    config["knowledge_prompt_lines"] = new_data["knowledge_prompt_lines"]

    # 加载可选的知识校对 ReAct prompt（知识场景专用，不存在时不报错）
    if "knowledge_agent_prompt_lines" in new_data:
        config["knowledge_agent_prompt_lines"] = new_data["knowledge_agent_prompt_lines"]

    # 加载可选的 agent_prompt.json（ReAct 模式专用，不存在时不报错）
    agent_file = os.path.join(profile_dir, "agent_prompt.json")
    if os.path.exists(agent_file):
        try:
            with open(agent_file, 'r', encoding='utf-8') as f:
                agent_data = json.load(f)
            config["agent_prompt_lines"] = agent_data.get("agent_prompt_lines", [])
        except Exception as e:
            _log.warning(f"加载 agent_prompt.json 失败: {e}")

    lecture = new_data.get("lecture_split", {})
    config["lecture_split_mode"] = lecture.get("split_mode", "title")
    config["lecture_section_pattern"] = lecture.get("section_pattern", r"^##\s")
    config["lecture_wrapped_patterns"] = lecture.get("wrapped_patterns", [])
    config["lecture_unwrapped_patterns"] = lecture.get("unwrapped_patterns", [])
    config["lecture_section_boundary"] = lecture.get("section_boundary", True)

    exam = new_data.get("exam_split", {})
    config["exam_question_pattern"] = exam.get("question_pattern", r"^(\d+)．")

    # v0.2.0 配置段：透传（base_profile.py 通过 self.config.get 读取默认值）
    for section in ("split", "proofread", "review"):
        config[section] = new_data.get(section, {})

    _config_cache[cache_key] = config
    return config


def get_question_prompt(config):
    prompt = config["question_prompt_lines"]
    if isinstance(prompt, list):
        prompt = "\n".join(prompt)
    return prompt


def get_knowledge_prompt(config):
    prompt = config["knowledge_prompt_lines"]
    if isinstance(prompt, list):
        prompt = "\n".join(prompt)
    return prompt


def get_lecture_patterns(config):
    wrapped = []
    for pat in config["lecture_wrapped_patterns"]:
        try:
            full_pat = r'^\*\*' + pat + r'\*\*.*$'
            wrapped.append(re.compile(full_pat))
        except re.error:
            _log.warning("无效正则 (wrapped): %r", pat)
    unwrapped = []
    for pat in config["lecture_unwrapped_patterns"]:
        try:
            unwrapped.append(re.compile(pat))
        except re.error:
            _log.warning("无效正则 (unwrapped): %r", pat)
    return wrapped, unwrapped


def get_compiled_title_patterns(config):
    wrapped, unwrapped = get_lecture_patterns(config)
    return wrapped + unwrapped


def get_section_boundary_enabled(config):
    return config.get("lecture_section_boundary", True)


def get_lecture_split_mode(config):
    return config.get("lecture_split_mode", "title")


def get_section_pattern(config):
    try:
        return re.compile(config.get("lecture_section_pattern", r"^##\s"))
    except re.error:
        return re.compile(r"^##\s")


def get_exam_question_pattern(config):
    try:
        return re.compile(config["exam_question_pattern"])
    except re.error:
        return re.compile(r"^(\d+)．")



def get_agent_prompt(config):
    return config.get("agent_prompt_lines", None)
