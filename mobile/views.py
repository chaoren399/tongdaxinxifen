"""手机端路由。

统一挂在 ``/m`` 前缀下（Blueprint: ``mobile``）：
======================================  ===============================
``/m/``                                 首页（概览 + 快捷入口 + 最近搜索）
``/m/industry``                         一级行业列表
``/m/industry/<pid>``                   一级下的二级行业
``/m/industry/<pid>/<sid>``             二级下的三级行业
``/m/tertiary/<tid>/stocks``            三级行业下的股票
``/m/stock/<sid>``                      股票详情（含图片、选项字段）
``/m/search?q=``                        股票搜索
``/m/more``                             更多 / 版本切换
======================================  ===============================

移动端只做**浏览与搜索**，管理类功能（页面 C/E/K 的新增编辑）仍在 PC 端完成，
保持手机端轻量。后续要迭代可在 ``feature_flags`` 里逐步开放。
"""
from flask import abort, jsonify, redirect, render_template, request, url_for

from db import get_recent_search_history

from . import data

# TabBar 四个入口（后续迭代只需往这里加）
TABS = [
    {"key": "home", "label": "首页", "endpoint": "mobile.home", "icon": "🏠"},
    {"key": "industry", "label": "行业", "endpoint": "mobile.industry", "icon": "🗂"},
    {"key": "search", "label": "搜索", "endpoint": "mobile.search", "icon": "🔍"},
    {"key": "more", "label": "我的", "endpoint": "mobile.more", "icon": "☰"},
]


def _render(template, **ctx):
    """统一注入 TabBar 与页面标题等公共变量。"""
    ctx.setdefault("tabs", TABS)
    ctx.setdefault("active_tab", "home")
    return render_template(template, **ctx)


def register(bp):
    """把所有手机端路由注册到 Blueprint 上。"""

    # ------------------------------------------------------------ 首页
    @bp.route("/")
    def home():
        stats = data.overview_stats()
        primaries = data.list_primaries()[:8]
        try:
            history = get_recent_search_history(limit=10)
        except Exception:
            history = []
        return _render(
            "mobile/home.html",
            stats=stats,
            primaries=primaries,
            history=history,
            active_tab="home",
        )

    # ------------------------------------------------------------ 行业下钻
    @bp.route("/industry")
    def industry():
        primaries = data.list_primaries()
        return _render(
            "mobile/level_list.html",
            level=1,
            items=primaries,
            crumbs=[{"label": "一级行业", "url": None}],
            active_tab="industry",
        )

    @bp.route("/industry/<int:pid>")
    def secondary(pid):
        parent = data.get_industry(pid)
        if parent is None or parent["level"] != 1:
            abort(404)
        items = data.list_children(pid)
        return _render(
            "mobile/level_list.html",
            level=2,
            items=items,
            crumbs=[
                {"label": "一级行业", "url": url_for("mobile.industry")},
                {"label": parent["name"], "url": None},
            ],
            back_url=url_for("mobile.industry"),
            active_tab="industry",
        )

    @bp.route("/industry/<int:pid>/<int:sid>")
    def tertiary(pid, sid):
        secondary_node = data.get_industry(sid)
        if secondary_node is None or secondary_node["parent_id"] != pid:
            abort(404)
        items = data.list_children(sid)
        return _render(
            "mobile/level_list.html",
            level=3,
            items=items,
            crumbs=[
                {"label": "一级行业", "url": url_for("mobile.industry")},
                {
                    "label": secondary_node["name"],
                    "url": url_for("mobile.secondary", pid=pid),
                },
                {"label": "三级行业", "url": None},
            ],
            back_url=url_for("mobile.secondary", pid=pid),
            active_tab="industry",
        )

    @bp.route("/tertiary/<int:tid>/stocks")
    def tertiary_stocks(tid):
        node = data.get_industry(tid)
        if node is None or node["level"] != 3:
            abort(404)
        stocks = data.list_tertiary_stocks(tid)
        path = data.industry_path(tid)
        crumbs = [
            {"label": n["name"], "url": _level_url(n)} for n in path[:-1]
        ] + [{"label": node["name"], "url": None}]

        parent_id = path[-2]["id"] if len(path) >= 2 else None
        back_url = (
            url_for("mobile.tertiary", pid=path[0]["id"], sid=parent_id)
            if len(path) >= 2
            else url_for("mobile.industry")
        )
        return _render(
            "mobile/stock_list.html",
            node=node,
            stocks=stocks,
            crumbs=crumbs,
            back_url=back_url,
            active_tab="industry",
        )

    @bp.route("/stock/<int:sid>")
    def stock_detail(sid):
        stock = data.get_stock(sid)
        if stock is None:
            abort(404)
        back_url = url_for(
            "mobile.tertiary_stocks", tid=stock["tertiary_id"]
        )
        return _render(
            "mobile/stock_detail.html",
            stock=stock,
            back_url=back_url,
            active_tab="industry",
        )

    # ------------------------------------------------------------ 搜索
    @bp.route("/search")
    def search():
        kw = (request.args.get("q") or "").strip()
        stocks = data.search_stocks(kw) if kw else []
        return _render(
            "mobile/search.html",
            keyword=kw,
            stocks=stocks,
            active_tab="search",
        )

    # 轻量接口：首页/搜索框的即时联想（后续可对接前端 AJAX）
    @bp.route("/api/suggest")
    def api_suggest():
        kw = (request.args.get("q") or "").strip()
        rows = data.search_stocks(kw, limit=20) if kw else []
        return jsonify(
            {
                "ok": True,
                "items": [
                    {
                        "id": r["id"],
                        "name": r["name"],
                        "code": r["code"],
                        "tertiary": r["tertiary_name"],
                        "url": url_for("mobile.stock_detail", sid=r["id"]),
                    }
                    for r in rows
                ],
            }
        )

    # ------------------------------------------------------------ 更多
    @bp.route("/more")
    def more():
        stats = data.overview_stats()
        return _render(
            "mobile/more.html",
            stats=stats,
            active_tab="more",
        )

    @bp.route("/go-pc")
    def go_pc():
        """PC 端入口的快捷写法：强制切回电脑版首页。"""
        return redirect(url_for("mobile.switch_view", to="pc", next="/"))

    return bp


def _level_url(node):
    """把行业节点转成对应列表页 URL。"""
    if node["level"] == 1:
        return url_for("mobile.secondary", pid=node["id"])
    if node["level"] == 2:
        parent = node.get("parent_id")
        return url_for("mobile.tertiary", pid=parent, sid=node["id"])
    return url_for("mobile.industry")
