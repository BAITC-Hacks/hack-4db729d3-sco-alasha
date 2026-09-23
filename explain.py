#!/usr/bin/env python3
"""Объяснение роли без UI: python explain.py [gid ...]; без аргументов — топ-3."""
from __future__ import annotations

import argparse
import sys

import graph_tools as T


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("gids", metavar="gid", nargs="*", type=int)
    args = parser.parse_args()
    gids = args.gids or [row["gid"] for row in T.top_nodes(n=3)]
    status = 0
    for gid in gids:
        print(T.node_card(gid))
        if gid not in T.DF().index:
            status = 1
            continue
        # Один контрагент может быть и плательщиком, и получателем: складываем оборот.
        peers = {}
        nb = T.neighbors(gid, "both", limit=len(T.DF()))
        for direction, key in (("in", "payers"), ("out", "receivers")):
            for row in nb[key]:
                peer = peers.setdefault(row["gid"], {"in": 0.0, "out": 0.0})
                peer[direction] += row["sum_kzt"]
        top = sorted(peers.items(), key=lambda item: (-(item[1]["in"] + item[1]["out"]), item[0]))[:5]
        print("\n5 крупнейших контрагентов по сумме входа и выхода:")
        for peer_gid, sums in top:
            print(f"- {peer_gid}: вход {sums['in']:,.0f} ₸; выход {sums['out']:,.0f} ₸")
        if not top:
            print("Нет контрагентов в выгрузке.")
        print()
    return status


if __name__ == "__main__":
    raise SystemExit(main())
