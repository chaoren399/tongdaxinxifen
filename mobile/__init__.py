"""手机端子应用（Flask Blueprint）。

设计要点
--------
1. **完全隔离**：手机端的路由、模板、静态资源全部放在本目录（``mobile/``），
   不改动 ``templates/``、``static/`` 下任何 PC 端文件；
2. **数据共享**：数据访问统一复用 ``db.py``（同一份 ``data/tongdaxin.db``），
   不 import ``app.py``，避免循环依赖，也不需要同步 app 的内存树；
3. **零侵入接入**：主程序 ``app.py`` 只需两行即可挂载::

       from mobile import register_mobile
       register_mobile(app)

   挂载后：PC 端仍走 ``/``，手机端走 ``/m/``；手机 UA 访问任意 PC 页面会被
   自动引导到 ``/m/``（可用 cookie ``force_view=pc`` 关闭，或点底栏「电脑版」）。

目录约定
--------
::

    mobile/
      __init__.py              本文件：Blueprint 创建 / 注册 / UA 自动跳转
      data.py                  移动端的只读数据访问层（直接查 SQLite）
      views.py                 手机端所有路由
      templates/mobile/*.html  手机端模板（与主 templates/ 物理隔离）
      static/...               手机端静态资源（URL 前缀 /m/static/）
"""
import os
import re

from flask import Blueprint, redirect, request

# Blueprint 根目录（mobile/），模板与静态资源均相对它定位
_ROOT = os.path.dirname(os.path.abspath(__file__))
_TEMPLATES = os.path.join(_ROOT, "templates")
_STATIC = os.path.join(_ROOT, "static")

# 手机端 URL 前缀
MOBILE_PREFIX = "/m"
# 用于「强制 PC 版 / 强制手机版」的 cookie 名
VIEW_COOKIE = "force_view"
# cookie 有效期（天）
VIEW_COOKIE_MAX_AGE = 180 * 24 * 3600

# 判定为手机的 UA 关键字（命中即认为应进入手机端）
_MOBILE_UA_RE = re.compile(
    r"(android|iphone|ipod|windows phone|blackberry|bb10|"
    r"opera mini|iemobile|mobile safari|huawei|honor|xiaomi|redmi|vivo|oppo)",
    re.IGNORECASE,
)

# 这些前缀下的请求不做跳转：手机端自身、静态资源、股票图片、所有 API
_SKIP_PREFIXES = (
    MOBILE_PREFIX + "/",
    "/static/",
    "/stock_images/",
    "/api/",
    "/favicon",
)


def ua_is_mobile(ua):
    """判断 UA 字符串是否来自手机/移动端浏览器。"""
    return bool(ua and _MOBILE_UA_RE.search(ua))


def create_blueprint():
    """创建并返回手机端 Blueprint（内含全部路由）。"""
    bp = Blueprint(
        "mobile",
        __name__,
        url_prefix=MOBILE_PREFIX,
        template_folder=_TEMPLATES,
        static_folder=_STATIC,
        # 注意：Blueprint 会自动拼接 url_prefix，这里只写相对路径 → /m/static/
        static_url_path="static",
    )

    from . import views  # noqa: F401  延迟导入：views 依赖本模块已就绪的常量

    views.register(bp)
    _register_ua_switch(bp)
    return bp


def register_mobile(app):
    """把手机端挂到 Flask app 上（幂等，重复调用无副作用）。"""
    if app.blueprints.get("mobile") is None:
        app.register_blueprint(create_blueprint())

    # 供主程序模板判断是否展示「手机版」入口（可选使用）
    app.jinja_env.globals.setdefault("ua_is_mobile", ua_is_mobile)
    # 模板里按层级生成下钻链接
    app.jinja_env.globals.setdefault("m_item_url", level_item_url)
    return app


def level_item_url(item, level):
    """按当前层级计算出下一级的 URL。

    level=1 → 展示一级列表，点击进二级；level=2 → 进三级；level=3 → 股票列表。
    """
    from flask import url_for

    level = int(level or 1)
    if level == 1:
        return url_for("mobile.secondary", pid=item["id"])
    if level == 2:
        return url_for("mobile.tertiary", pid=item.get("parent_id"), sid=item["id"])
    return url_for("mobile.tertiary_stocks", tid=item["id"])


def _register_ua_switch(bp):
    """注册「手机 UA 自动进入 /m/」与「手动切换版本」逻辑。"""

    @bp.before_app_request
    def _auto_to_mobile():
        path = request.path or "/"

        # 已在手机端 / 属于静态资源、图片、API / 非 GET：一律不跳转
        if (
            path == MOBILE_PREFIX
            or path.startswith(_SKIP_PREFIXES)
            or request.method not in ("GET", "HEAD")
        ):
            return None

        forced = (request.cookies.get(VIEW_COOKIE) or "").lower()
        if forced == "pc":                       # 用户主动切到电脑版
            return None
        if forced != "mobile" and not ua_is_mobile(request.user_agent.string):
            return None                          # 普通 PC 浏览器不受影响

        return redirect(MOBILE_PREFIX + "/", code=302)

    @bp.route("/switch")
    def switch_view():
        """切换 PC / 手机版本，写入 cookie 后跳回目标地址。

        ``/m/switch?to=pc&next=/pageF``
        """
        from flask import abort, url_for as _url_for

        to = (request.args.get("to") or "mobile").lower()
        if to not in ("pc", "mobile"):
            abort(400)

        nxt = request.args.get("next") or ""
        # 只允许站内相对路径跳转，避免开放重定向
        if not nxt.startswith("/") or nxt.startswith("//"):
            nxt = "/" if to == "pc" else MOBILE_PREFIX + "/"

        resp = redirect(nxt)
        resp.set_cookie(
            VIEW_COOKIE, to, max_age=VIEW_COOKIE_MAX_AGE, httponly=False
        )
        _ = _url_for  # 保持风格一致（未来如需按端点跳转可直接使用）
        return resp

    return bp
