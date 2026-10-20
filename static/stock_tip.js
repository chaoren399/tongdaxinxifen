/* 全局股票名称悬浮提示：鼠标放到 [data-stock] 元素上，显示该股票
   「选项B」的前 10 个字符（来自 /api/stock_tip）。

   任何页面只要给股票名元素加上 data-stock="股票名" 即可启用，
   无需每个页面单独写逻辑。 */
(function () {
    "use strict";

    var cache = Object.create(null);   // 名称 -> {loading, data}
    var el = null;                      // 提示浮层
    var curTarget = null;
    var hideTimer = null;

    function ensureTip() {
        if (el) return el;
        el = document.createElement("div");
        el.className = "stk-tip";
        el.setAttribute("role", "tooltip");
        document.body.appendChild(el);
        return el;
    }

    function showTip(target, x, y) {
        ensureTip();
        var name = target.getAttribute("data-stock") || target.textContent.trim();
        if (!name) return;
        var industry = target.getAttribute("data-industry") || "";

        clearTimeout(hideTimer);
        el.innerHTML = "";
        var nameSpan = document.createElement("span");
        nameSpan.className = "stk-tip-name";
        nameSpan.textContent = name;
        el.appendChild(nameSpan);

        var cached = cache[name];
        if (cached && cached.data) {
            renderBody(cached.data, cached.industry);
        } else if (cached && cached.loading) {
            appendText("加载中…");
        } else {
            appendText("加载中…");
            cache[name] = { loading: true, data: null, industry: industry };
            fetch("/api/stock_tip?name=" + encodeURIComponent(name))
                .then(function (r) { return r.json(); })
                .then(function (j) {
                    cache[name] = { loading: false, data: j, industry: j.industry || industry };
                    if (curTarget && curTarget.getAttribute("data-stock") === name) {
                        renderBody(j, j.industry || industry);
                    }
                })
                .catch(function () {
                    cache[name] = { loading: false, data: { ok: true, found: false }, industry: industry };
                });
        }

        position(x, y);
        el.classList.add("is-visible");
    }

    function appendText(text) {
        el.appendChild(document.createTextNode(text));
    }

    function renderBody(j, industry) {
        el.innerHTML = "";
        var nameSpan = document.createElement("span");
        nameSpan.className = "stk-tip-name";
        nameSpan.textContent = j.name || "";
        el.appendChild(nameSpan);

        if (!j.found || !j.option_b) {
            var empty = document.createElement("span");
            empty.className = "stk-tip-empty";
            empty.textContent = "（无选项B）";
            el.appendChild(empty);
        } else {
            el.appendChild(document.createTextNode(j.preview || ""));
        }

        // 选项D 前 10 个字符（不显示"选项D："标签，仅展示内容）
        if (j.found && j.option_d) {
            var dWrap = document.createElement("div");
            dWrap.className = "stk-tip-option-d";
            dWrap.textContent = j.preview_d || "";
            el.appendChild(dWrap);
        }

        // 行业路径（优先来自接口，其次页面 data-industry，合并进选项B提示）
        if (industry) {
            var ind = document.createElement("div");
            ind.className = "stk-tip-industry";
            ind.textContent = "行业：" + industry;
            el.appendChild(ind);
        }
    }

    function position(x, y) {
        var rect = el.getBoundingClientRect();
        var left = x + 14;
        var top = y + 18;
        if (left + rect.width > window.innerWidth - 8) {
            left = x - rect.width - 14;
        }
        if (top + rect.height > window.innerHeight - 8) {
            top = y - rect.height - 14;
        }
        if (left < 8) left = 8;
        if (top < 8) top = 8;
        el.style.left = left + "px";
        el.style.top = top + "px";
    }

    function hideTip() {
        if (!el) return;
        el.classList.remove("is-visible");
    }

    function findTarget(node) {
        while (node && node !== document) {
            if (node.getAttribute && node.getAttribute("data-stock") !== null) {
                return node;
            }
            node = node.parentNode;
        }
        return null;
    }

    document.addEventListener("mouseover", function (e) {
        var t = findTarget(e.target);
        if (!t) { return; }
        curTarget = t;
        showTip(t, e.clientX, e.clientY);
    });

    document.addEventListener("mouseout", function (e) {
        var t = findTarget(e.target);
        if (!t) return;
        // 移到子元素时不隐藏
        if (e.relatedTarget && t.contains(e.relatedTarget)) return;
        curTarget = null;
        hideTimer = setTimeout(hideTip, 80);
    });

    document.addEventListener("mousemove", function (e) {
        if (el && el.classList.contains("is-visible") && curTarget) {
            position(e.clientX, e.clientY);
        }
    });

    // 键盘可达性：聚焦到带 data-stock 的元素时显示
    document.addEventListener("focusin", function (e) {
        var t = findTarget(e.target);
        if (!t) return;
        var r = t.getBoundingClientRect();
        curTarget = t;
        showTip(t, r.left + r.width / 2, r.top);
    });
    document.addEventListener("focusout", function () {
        curTarget = null;
        hideTip();
    });
})();
