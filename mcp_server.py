"""
MCP-сервер «graph-intel»: инструменты графа расследования для ЛЮБОГО MCP-клиента
(Claude Desktop, Cursor, VS Code, внутренний бот банка, n8n).

Запуск:
  python mcp_server.py                 # stdio (для Claude Desktop / Cursor)
  python mcp_server.py --http          # Streamable HTTP: http://127.0.0.1:8010/mcp
Требует out/graph.pkl (python pipeline.py).  SDK: mcp < 2 (FastMCP).
"""
from __future__ import annotations

import sys

from mcp.server.fastmcp import FastMCP

import graph_tools as T

mcp = FastMCP("graph-intel", host="127.0.0.1", port=8010)


@mcp.tool()
def node_info(gid: str) -> dict:
    """Роль (coordinator/consolidator/distributor/transit/terminal/peripheral), уверенность, кластер,
    приоритет, метрики и человекочитаемое обоснование по одному клиенту gid."""
    return T.node_info(gid)


@mcp.tool()
def neighbors(gid: str, direction: str = "both", limit: int = 15) -> dict:
    """Контрагенты клиента: direction='in' — кто ему платил, 'out' — кому платил он, 'both' — оба. С суммами."""
    return T.neighbors(gid, direction, limit)


@mcp.tool()
def trace_money(gid: str, max_depth: int = 4) -> dict:
    """Куда уходят деньги клиента вниз по цепочке переводов (крупнейшие потоки на каждом шаге)."""
    return T.trace_money(gid, max_depth)


@mcp.tool()
def common_receivers(gids: list[str], max_depth: int = 3) -> dict:
    """Кто собирает деньги с нескольких указанных клиентов: узлы, куда за <= max_depth переводов
    доходят деньги от двух и более из них. Ответ на вопрос «кто стоит над этими курьерами»."""
    return T.common_receivers(gids, max_depth)


@mcp.tool()
def top_nodes(role: str | None = None, n: int = 10, exclude_seeds: bool = False) -> list:
    """Топ клиентов по приоритету углублённой проверки, опционально по роли."""
    return T.top_nodes(role, n, exclude_seeds)


@mcp.tool()
def cluster_info(cluster_id: int) -> dict:
    """Кластер (сообщество) сети: размер, число seed, внутренний оборот, гипотеза, ключевые узлы."""
    return T.cluster_info(cluster_id)


@mcp.tool()
def simulate_removal(gids: list[str] | None = None, top_n: int = 10) -> dict:
    """Стресс-тест: что станет с сетью, если заблокировать указанные gid (или топ-N по приоритету)."""
    return T.simulate_removal(gids, top_n)


@mcp.tool()
def node_card(gid: str) -> str:
    """Готовая справка по клиенту (markdown): роль, потоки, крупнейшие контрагенты, на что обратить внимание."""
    return T.node_card(gid)


@mcp.tool()
def data_gaps(n: int = 15) -> list:
    """Оценка полноты: какие данные запросить следующими, с gid, основаниями и приоритетом проверки."""
    return T.data_gaps(n)


@mcp.resource("graph://methodology")
def methodology() -> str:
    """Пороги ролей, веса приоритета и параметры модели для 4-го колена."""
    return (T.OUT / "run_meta.json").read_text(encoding="utf-8")


@mcp.resource("graph://node/{gid}")
def node_card_resource(gid: str) -> str:
    """Справка по клиенту в markdown — для запроса в правоохранительные органы / углублённой проверки."""
    return T.node_card(gid)


@mcp.prompt()
def investigate(gids: str) -> str:
    """Шаблон расследования по списку gid через пробел."""
    return (f"Проанализируй клиентов {gids}: найди общих получателей их денег (common_receivers), "
            f"для топ-3 найденных узлов открой node_info и trace_money, оцени, что даст их блокировка "
            f"(simulate_removal). Формулируй выводы как гипотезы для проверки, с gid и цифрами.")


if __name__ == "__main__":
    mcp.run(transport="streamable-http" if "--http" in sys.argv else "stdio")
