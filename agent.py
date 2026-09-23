"""
AI-аналитик: вопрос на естественном языке -> LLM сама выбирает инструменты -> вызовы идут ЧЕРЕЗ MCP
(MCP-клиент -> MCP-сервер graph-intel) -> ответ со ссылками на gid.

  * Список инструментов агент получает у MCP-сервера (tools/list), вызывает через tools/call.
  * Если MCP-сервер недоступен — те же функции вызываются напрямую (graph_tools), via="direct".
  * LLM: любой OpenAI-совместимый API (OPENAI_API_KEY, опционально OPENAI_BASE_URL / OPENAI_MODEL).
    Без ключа или при ошибке API — детерминированный режим правил на тех же инструментах,
    чтобы основной сценарий проверялся без личных аккаунтов (п. 5.6.6 положения).
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time

import graph_tools as T

try:  # .env подхватывается, если установлен python-dotenv
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

MCP_URL = os.getenv("MCP_URL", "http://127.0.0.1:8010/mcp")

SYSTEM = (
    "Ты — AI-ассистент AML-аналитика банка. Работаешь с графом внутрибанковских переводов (июль 2026), "
    "собранным от 81 seed-клиента на 4 колена по исходящим переводам. Отвечай по-русски, кратко, по делу, в markdown. "
    "Используй ТОЛЬКО данные из инструментов: никаких выдуманных фактов, связей и атрибутов клиентов. "
    "Всегда указывай gid полностью и цифры (суммы в ₸, число плательщиков/получателей, доли). "
    "Формулируй выводы как гипотезы для проверки («признаки консолидации»), а не как утверждение о виновности. "
    "Роли: coordinator (кандидат в организаторы), consolidator (сбор средств), distributor (веерная рассылка), "
    "transit (пропускает дальше), terminal (исходящих не наблюдается), peripheral. "
    "Помни: у узлов 4-го колена исходящие не выгружены. В конце предложи следующий шаг проверки."
)


# ------------------------------------------------------------------ мост к инструментам: MCP или напрямую
def _parse_mcp_result(res):
    if getattr(res, "structuredContent", None):
        sc = res.structuredContent
        return sc["result"] if isinstance(sc, dict) and set(sc) == {"result"} else sc
    texts = [c.text for c in res.content if getattr(c, "type", "") == "text"]
    parsed = []
    for t in texts:
        try:
            parsed.append(json.loads(t))
        except (json.JSONDecodeError, TypeError):
            parsed.append(t)
    return parsed[0] if len(parsed) == 1 else parsed


class ToolBridge:
    """async with ToolBridge() as tb: await tb.call(name, args)."""

    def __init__(self, url: str = MCP_URL):
        self.url, self.session, self._stack, self.via = url, None, None, "direct"
        self.mcp_tools = []

    async def __aenter__(self):
        from contextlib import AsyncExitStack
        self._stack = AsyncExitStack()
        try:
            from mcp import ClientSession
            from mcp.client.streamable_http import streamablehttp_client
            read, write, _ = await asyncio.wait_for(
                self._stack.enter_async_context(streamablehttp_client(self.url, timeout=5)), 6)
            self.session = await self._stack.enter_async_context(ClientSession(read, write))
            await asyncio.wait_for(self.session.initialize(), 6)
            self.mcp_tools = (await self.session.list_tools()).tools
            self.via = "mcp"
        except BaseException:  # noqa: BLE001 — сервер не поднят: работаем напрямую
            try:
                await self._stack.aclose()
            except BaseException:  # noqa: BLE001
                pass
            self._stack, self.session, self.via = None, None, "direct"
        return self

    async def __aexit__(self, *exc):
        if self._stack:
            try:
                await self._stack.aclose()
            except BaseException:  # noqa: BLE001
                pass

    def openai_tools(self) -> list:
        if self.via == "mcp":
            return [{"type": "function", "function": {"name": t.name, "description": t.description or "",
                                                      "parameters": t.inputSchema}} for t in self.mcp_tools]
        return [{"type": "function", "function": s} for s in FALLBACK_SCHEMAS]

    async def call(self, name: str, args: dict) -> dict:
        t0 = time.time()
        via = self.via
        try:
            if self.via == "mcp":
                result = _parse_mcp_result(await self.session.call_tool(name, args))
            else:
                result = T.TOOLS[name](**args)
        except Exception as e:  # noqa: BLE001
            result = {"error": f"{type(e).__name__}: {e}"}
        return {"tool": name, "args": args, "via": via, "ms": int((time.time() - t0) * 1000), "result": result}


GID = {"type": "string", "description": "gid клиента (длинное целое число, передавай строкой)"}
FALLBACK_SCHEMAS = [
    {"name": "node_info", "description": "Роль, скоры, кластер, метрики и обоснование по одному узлу.",
     "parameters": {"type": "object", "properties": {"gid": GID}, "required": ["gid"]}},
    {"name": "node_card", "description": "Справка по клиенту: роль, потоки, контрагенты, на что обратить внимание.",
     "parameters": {"type": "object", "properties": {"gid": GID}, "required": ["gid"]}},
    {"name": "neighbors", "description": "Кто платил узлу (in) и кому платил узел (out), с суммами.",
     "parameters": {"type": "object", "properties": {"gid": GID, "direction": {"type": "string", "enum": ["in", "out", "both"]},
                                                     "limit": {"type": "integer"}}, "required": ["gid"]}},
    {"name": "trace_money", "description": "Куда уходят деньги узла вниз по цепочке переводов.",
     "parameters": {"type": "object", "properties": {"gid": GID, "max_depth": {"type": "integer"}}, "required": ["gid"]}},
    {"name": "common_receivers", "description": "Кто собирает деньги с нескольких указанных клиентов.",
     "parameters": {"type": "object", "properties": {"gids": {"type": "array", "items": GID},
                                                     "max_depth": {"type": "integer"}}, "required": ["gids"]}},
    {"name": "top_nodes", "description": "Топ узлов по приоритету проверки, опционально по роли.",
     "parameters": {"type": "object", "properties": {"role": {"type": "string"}, "n": {"type": "integer"},
                                                     "exclude_seeds": {"type": "boolean"}}}},
    {"name": "cluster_info", "description": "Состав и гипотеза кластера.",
     "parameters": {"type": "object", "properties": {"cluster_id": {"type": "integer"}}, "required": ["cluster_id"]}},
    {"name": "simulate_removal", "description": "Что станет с сетью при блокировке узлов (список gid или топ-N).",
     "parameters": {"type": "object", "properties": {"gids": {"type": "array", "items": GID}, "top_n": {"type": "integer"}}}},
    {"name": "data_gaps", "description": "Каких данных не хватает и какие запросы сделать следующими, по приоритету узлов.",
     "parameters": {"type": "object", "properties": {"n": {"type": "integer", "minimum": 0}}}},
]


def _gids_in(obj) -> list[int]:
    s = json.dumps(obj, ensure_ascii=False, default=str)
    found, seen = [], set()
    for x in re.findall(r"\d{15,20}", s):
        g = int(x)
        if g in T.DF().index and g not in seen:
            seen.add(g)
            found.append(g)
    return found


# ------------------------------------------------------------------ режим правил (без LLM)
async def _rule_based(q_raw: str, tb: ToolBridge) -> dict:
    q = q_raw.lower()
    gids = re.findall(r"\d{15,20}", q_raw)
    steps = []
    if any(w in q for w in ("удал", "изъ", "заблок", "блокир")):
        n = int((re.findall(r"топ[- ]?(\d+)", q) or [10])[0])
        s = await tb.call("simulate_removal", {"gids": gids or None, "top_n": n})
        steps.append(s)
        r = s["result"]
        ans = (f"При блокировке **{len(r['removed'])} узлов** достижимость сети от seed падает на "
               f"**{r['drop_pct']['seed_reach_pairs']}%**, оборот в графе — на {r['drop_pct']['flow_kzt']}%, "
               f"крупнейшая компонента — на {r['drop_pct']['largest_component']}%.")
    elif not gids and any(w in q for w in ("полнот", "запросить", "не хватает", "белые пятна")):
        s = await tb.call("data_gaps", {"n": 15})
        steps.append(s)
        ans = "**Какие данные запросить следующими:**\n\n" + "\n".join(
            f"{x['rank']}. **{x['gid']}**: {x['request']}. {x['reason']}"
            for x in s["result"])
    elif len(gids) >= 2:
        s = await tb.call("common_receivers", {"gids": gids})
        steps.append(s)
        c = s["result"].get("collectors", [])
        if c:
            ans = "**Общие получатели денег указанных клиентов** (гипотезы для проверки):\n\n" + "\n".join(
                f"- **{x['gid']}**: {x['role_ru']}, деньги {x['from_n_of_given']} из {s['result']['given']} указанных. {x['evidence']}"
                for x in c[:5])
            s2 = await tb.call("trace_money", {"gid": str(c[0]["gid"]), "max_depth": 2})
            steps.append(s2)
            ans += f"\n\nСледующий шаг: проверить узел **{c[0]['gid']}** и его получателей (путь денег на схеме)."
        else:
            ans = "У указанных клиентов нет общих получателей в пределах 3 переводов."
    elif len(gids) == 1:
        s = await tb.call("node_card", {"gid": gids[0]})
        steps.append(s)
        ans = s["result"] if isinstance(s["result"], str) else json.dumps(s["result"], ensure_ascii=False)
    else:
        role = next((ro for ro, ru in T.ROLE_RU.items() if ru.split()[0][:5] in q or ro in q), None)
        s = await tb.call("top_nodes", {"role": role, "n": 7, "exclude_seeds": True})
        steps.append(s)
        ans = "**Кого смотреть первым** (топ по приоритету, без seed):\n\n" + "\n".join(
            f"{i + 1}. **{x['gid']}**: {x['role_ru']} ({x['priority_score']:.2f}). {x['evidence']}"
            for i, x in enumerate(s["result"]))
    return {"answer": ans + "\n\n_Режим без LLM: ответ собран правилами из тех же MCP-инструментов._",
            "steps": steps, "mode": "rules"}


# ------------------------------------------------------------------ LLM-агент
async def _llm(question: str, tb: ToolBridge, max_steps: int = 6) -> dict:
    from openai import AsyncOpenAI
    key = os.getenv("OPENAI_API_KEY") or os.getenv("NVIDIA_API_KEY")
    client = AsyncOpenAI(api_key=key, base_url=os.getenv("OPENAI_BASE_URL") or None, timeout=45)
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": question}]
    steps = []
    for _ in range(max_steps):
        resp = await client.chat.completions.create(model=model, messages=msgs, tools=tb.openai_tools(), temperature=0)
        m = resp.choices[0].message
        if not m.tool_calls:
            return {"answer": m.content or "", "steps": steps, "mode": f"llm:{model}"}
        msgs.append({"role": "assistant", "content": m.content or "",
                     "tool_calls": [tc.model_dump() for tc in m.tool_calls]})
        for tc in m.tool_calls:
            args = json.loads(tc.function.arguments or "{}")
            s = await tb.call(tc.function.name, args)
            steps.append(s)
            msgs.append({"role": "tool", "tool_call_id": tc.id,
                         "content": json.dumps(s["result"], ensure_ascii=False, default=str)[:12000]})
    return {"answer": "Достигнут лимит шагов агента.", "steps": steps, "mode": f"llm:{model}"}


async def ask_async(question: str) -> dict:
    t0 = time.time()
    async with ToolBridge() as tb:
        use_llm = (os.getenv("OPENAI_API_KEY") or os.getenv("NVIDIA_API_KEY")) and os.getenv("LLM_MODE", "").lower() != "mock"
        if use_llm:
            try:
                res = await _llm(question, tb)
            except Exception as e:  # noqa: BLE001 — LLM недоступна: не падаем
                res = await _rule_based(question, tb)
                res["answer"] = f"_LLM недоступна ({type(e).__name__}), ответ по правилам._\n\n" + res["answer"]
        else:
            res = await _rule_based(question, tb)
        res["transport"] = tb.via
    res["highlight"] = _gids_in([s["result"] for s in res["steps"]] + [res["answer"]])[:40]
    res["ms"] = int((time.time() - t0) * 1000)
    return res


def ask(question: str) -> dict:
    """Синхронная обёртка (для Streamlit)."""
    res = asyncio.run(ask_async(question))
    res["steps"] = [(s["tool"], s["args"], s["result"]) for s in res["steps"]]
    return res


if __name__ == "__main__":
    import sys
    r = asyncio.run(ask_async(" ".join(sys.argv[1:]) or "кого смотреть первым?"))
    print(f"[transport={r['transport']} mode={r['mode']} {r['ms']} ms]")
    for s in r["steps"]:
        print(f"  -> {s['tool']}({s['args']}) via {s['via']} {s['ms']} ms")
    print(r["answer"])
