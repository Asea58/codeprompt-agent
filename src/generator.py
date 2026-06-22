"""Build the generation prompt from config, and parse the LLM's tagged output
into the four-piece structure (query / approach / code / answer_unit)."""
import re

from . import config_loader, llm_client


def _format_list(items, indent="    - "):
    return "\n".join(f"{indent}{x}" for x in items)


def build_prompts(subject, reason_key, extra_requirements=None):
    """Return (system_prompt, user_prompt) ready to send to the LLM."""
    subj = config_loader.load_subject(subject)
    reason = config_loader.load_reason(reason_key)

    system_prompt = config_loader.load_template("system_prompt")
    gen_template = config_loader.load_template("generation_prompt")

    must_have_block = _format_list(reason.get("must_have", []), indent="      - ")

    if extra_requirements:
        extra_block = f"## 额外要求（用户/重试反馈）\n{extra_requirements}\n"
    else:
        extra_block = ""

    user_prompt = gen_template.format(
        subject=subj["subject"],
        domain_description=subj["domain_description"],
        typical_scenarios="；".join(subj["typical_scenarios"]),
        common_symbols=subj["common_symbols"],
        typical_units=subj["typical_units"],
        style_notes=subj["style_notes"],
        reason_label=reason["label"],
        math_structure=reason["math_structure"],
        numerical_method=reason["numerical_method"],
        must_have_block=must_have_block,
        answer_pattern=reason["answer_pattern"],
        pitfalls=reason["pitfalls"],
        extra_requirements_block=extra_block,
    )
    return system_prompt, user_prompt


# tag key -> uppercase tag name used in the template
_TAGS = {
    "query": "QUERY",
    "approach": "APPROACH",
    "code": "CODE",
    "answer_unit": "ANSWER_UNIT",
    "i_checklist": "I_CHECKLIST",
    "checklist_new": "CHECKLIST_NEW",
}
# Sections that must be present; the checklists are best-effort (don't hard-fail
# generation if the model omits them — heuristics will warn instead).
_REQUIRED = {"query", "approach", "code", "answer_unit"}
# matches any opening tag — used by the fallback to know where a section ends
_ANY_OPEN = r"<(?:QUERY|APPROACH|CODE|ANSWER_UNIT|I_CHECKLIST|CHECKLIST_NEW)>"


def parse_output(raw):
    """Extract the tagged sections.

    Tolerant by design: if a section's closing tag is missing/mangled (a common
    LLM slip, e.g. dropping </QUERY>), fall back to capturing from the opening
    tag up to the next opening tag (or end of text). Only raises if a *required*
    section is genuinely absent; the checklist sections default to "".
    """
    result = {}
    missing = []
    for key, tag in _TAGS.items():
        # 1) well-formed <TAG>...</TAG>
        m = re.search(rf"<{tag}>(.*?)</{tag}>", raw, re.DOTALL | re.IGNORECASE)
        if m and m.group(1).strip():
            result[key] = m.group(1).strip()
            continue
        # 2) fallback: <TAG> ... up to next opening tag or end (missing close tag)
        m = re.search(rf"<{tag}>(.*?)(?:{_ANY_OPEN}|\Z)", raw, re.DOTALL | re.IGNORECASE)
        if m and m.group(1).strip():
            result[key] = m.group(1).strip()
            continue
        result[key] = ""
        if key in _REQUIRED:
            missing.append(key)

    if missing:
        raise ValueError(
            f"LLM 输出缺少标签: {missing}。原始输出前 800 字:\n{raw[:800]}"
        )
    # strip a possible ```python fence inside <CODE>
    result["code"] = _strip_code_fence(result["code"])
    return result


def _strip_code_fence(code):
    code = code.strip()
    if code.startswith("```"):
        code = re.sub(r"^```[a-zA-Z]*\n", "", code)
        code = re.sub(r"\n```$", "", code)
    return code.strip()


def generate(subject, reason_key, extra_requirements=None, mock=False):
    """One LLM round: returns parsed dict {query, approach, code, answer_unit}."""
    system_prompt, user_prompt = build_prompts(subject, reason_key, extra_requirements)
    raw = llm_client.call_llm(system_prompt, user_prompt, mock=mock)
    return parse_output(raw)
