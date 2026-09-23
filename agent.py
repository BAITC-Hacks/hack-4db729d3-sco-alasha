"""
AI-аналитик: вопрос на естественном языке -> агент сам вызывает инструменты графа (graph_tools)
-> ответ со ссылками на gid.

LLM: любой OpenAI-совместимый API (OPENAI_API_KEY, опционально OPENAI_BASE_URL / OPENAI_MODEL —
например NVIDIA API). Если ключа нет или API упал — детерминированный fallback на правилах,
чтобы сценарий проверялся без личных аккаунтов (п. 5.6.6 положения).
"""
from __future__ import annotations

import json
import os
import re

import graph_tools as T

try:  # .env подхватывается, если установлен python-dotenv
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

SYSTEM = (
    "Ты — AI-ассистент AML-аналитика банка. Отвечай по-русски, кратко и по делу. "
    "Используй ТОЛЬКО данные из инструментов, никаких выдуманных фактов и атрибутов клиентов. "
    "Всегда указывай gid узлов и цифры (суммы, число плательщиков). "
    "Формулируй выводы как гипотезы для проверки («признаки консолидации»), а не как утверждение о виновности. "
    "Роли: coordinator, consolidator, distributor, transit, terminal, peripheral."
)

GID = {"type": "string", "description": "gid клиента (длинное целое число, передавай строкой)"}
TOOL_SCHEMAS = [
    {"name": "node_info", "description": "Роль, скоры, кластер, метрики и обоснование по одному узлу.",
     "parameters": {"type": "object", "properties": {"gid": GID}, "required": ["gid"]}},
    {"name": "neighbors", "description": "Кто платил узлу (in) и кому платил узел (out), с суммами.",
     "parameters": {"type": "object", "properties": {"gid": GID, "direction": {"type": "string", "enum": ["in", "out", "both"]},
                                                     "limit": {"type": "integer"}}, "required": ["gid"]}},
    {"name": "trace_money", "description": "Куда уходят деньги узла вниз по цепочке переводов.",
     "parameters": {"type": "object", "properties": {"gid": GID, "max_depth": {"type": "integer"}}, "required": ["gid"]}},
    {"name": "common_receivers", "description": "Кто собирает деньги с нескольких указанных клиентов (общие получатели в пределах N переводов).",
     "parameters": {"type": "object", "properties": {"gids": {"type": "array", "items": GID},
                                                     "max_depth": {"type": "integer"}}, "required": ["gids"]}},
    {"name": "top_nodes", "description": "Топ узлов по приоритету проверки, опционально по роли.",
     "parameters": {"type": "object", "properties": {"role": {"type": "string", "enum": ["coordinator", "consolidator", "distributor", "transit", "terminal", "peripheral"]},
                                                     "n": {"type": "integer"}, "exclude_seeds": {"type": "boolean"}}}},
    {"name": "cluster_info", "description": "Состав и гипотеза кластера.",
     "parameters": {"type": "object", "properties": {"cluster_id": {"type": "integer"}}, "required": ["cluster_id"]}},
    {"name": "simulate_removal", "description": "Что станет с сетью при блокировке узлов (список gid или топ-N).",
     "parameters": {"type": "object", "properties": {"gids": {"type": "array", "items": GID}, "top_n": {"type": "integer"}}}},
]


def _call_tool(name: str, args: dict):
    try:
        return T.TOOLS[name](**args)
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"}


def _gids_in(obj) -> set[int]:
    s = json.dumps(obj, ensure_ascii=False, default=str)
    return {int(x) for x in re.findall(r"\b\d{12,20}\b", s) if int(x) in T.DF().index}


# ------------------------------------------------------------------ fallback без LLM
def rule_based(question: str) -> dict:
    q = question.lower()
    gids = [int(x) for x in re.findall(r"\d{12,20}", question)]
    steps = []
    if "удал" in q or "изъ" in q or "заблок" in q:
        n = int((re.findall(r"топ[- ]?(\d+)", q) or [10])[0])
        r = _call_tool("simulate_removal", {"gids": gids or None, "top_n": n})
        steps.append(("simulate_removal", {"top_n": n}, r))
        ans = (f"При блокировке {len(r['removed'])} узлов крупнейшая компонента сокращается на "
               f"{r['drop_pct']['largest_component']}%, оборот в графе — на {r['drop_pct']['flow_kzt']}%, "
               f"достижимость seed→узел — на {r['drop_pct']['seed_reach_pairs']}%.")
    elif len(gids) >= 2:
        r = _call_tool("common_receivers", {"gids": gids})
        steps.append(("common_receivers", {"gids": gids}, r))
        c = r["collectors"]
        if c:
            ans = "Общие получатели денег указанных клиентов (гипотезы для проверки):\n" + "\n".join(
                f"- **{x['gid']}** — {x['role_ru']}, деньги {x['from_n_of_given']} из {r['given']} указанных; {x['evidence']}"
                for x in c[:5])
        else:
            ans = "У указанных клиентов нет общих получателей в пределах 3 переводов."
    elif len(gids) == 1:
        r = _call_tool("node_card", {"gid": gids[0]})
        steps.append(("node_card", {"gid": gids[0]}, r))
        ans = r
    else:
        role = next((ro for ro, ru in T.ROLE_RU.items() if ru.split()[0][:5] in q or ro in q), None)
        r = _call_tool("top_nodes", {"role": role, "n": 7, "exclude_seeds": True})
        steps.append(("top_nodes", {"role": role, "n": 7}, r))
        ans = "Кого смотреть первым (топ по приоритету, без seed):\n" + "\n".join(
            f"{i + 1}. **{x['gid']}** — {x['role_ru']} ({x['priority_score']:.2f}): {x['evidence']}" for i, x in enumerate(r))
    return {"answer": ans + "\n\n_Режим без LLM: ответ собран правилами из тех же инструментов._",
            "steps": steps, "highlight": sorted(_gids_in([s[2] for s in steps]))[:30], "mode": "rules"}


# ------------------------------------------------------------------ LLM-агент
def ask(question: str, max_steps: int = 6) -> dict:
    key = os.getenv("OPENAI_API_KEY") or os.getenv("NVIDIA_API_KEY")
    if not key or os.getenv("LLM_MODE", "").lower() == "mock":
        return rule_based(question)
    try:
        from openai import OpenAI
        client = OpenAI(api_key=key, base_url=os.getenv("OPENAI_BASE_URL") or None, timeout=40)
        model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        tools = [{"type": "function", "function": s} for s in TOOL_SCHEMAS]
        msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": question}]
        steps = []
        for _ in range(max_steps):
            resp = client.chat.completions.create(model=model, messages=msgs, tools=tools, temperature=0)
            m = resp.choices[0].message
            if not m.tool_calls:
                return {"answer": m.content, "steps": steps,
                        "highlight": sorted(_gids_in([s[2] for s in steps]) | _gids_in(m.content or ""))[:30],
                        "mode": f"llm:{model}"}
            msgs.append({"role": "assistant", "content": m.content or "",
                         "tool_calls": [tc.model_dump() for tc in m.tool_calls]})
            for tc in m.tool_calls:
                args = json.loads(tc.function.arguments or "{}")
                res = _call_tool(tc.function.name, args)
                steps.append((tc.function.name, args, res))
                msgs.append({"role": "tool", "tool_call_id": tc.id,
                             "content": json.dumps(res, ensure_ascii=False, default=str)[:12000]})
        return {"answer": "Достигнут лимит шагов агента.", "steps": steps, "highlight": [], "mode": "llm"}
    except Exception as e:  # noqa: BLE001 — LLM недоступна: не падаем, отвечаем правилами
        r = rule_based(question)
        r["answer"] = f"_LLM недоступна ({type(e).__name__}), ответ по правилам._\n\n" + r["answer"]
        return r


if __name__ == "__main__":
    import sys
    print(ask(" ".join(sys.argv[1:]) or "кого смотреть первым?")["answer"])
