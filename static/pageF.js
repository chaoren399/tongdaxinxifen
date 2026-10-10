/* 页面 F：多股票批量搜索，结果按页面 E 卡片格式展示 */
(function () {
    "use strict";

    var input = document.getElementById("batchSearchInput");
    var btn = document.getElementById("batchSearchBtn");
    var list = document.getElementById("batchResultList");
    var summary = document.getElementById("batchSummary");
    var lastKeywords = [];

    function escText(el, text, keywords) {
        // 将 text 填入 el，keywords 命中处用 <mark> 高亮（大小写不敏感）
        if (!text) return;
        if (!keywords || !keywords.length) {
            el.textContent = text;
            return;
        }
        var lower = text.toLowerCase();
        var i = 0;
        while (i < text.length) {
            // 找最早出现的命中位置
            var bestIdx = -1, bestKw = null;
            keywords.forEach(function (kw) {
                var idx = lower.indexOf(kw, i);
                if (idx >= 0 && (bestIdx < 0 || idx < bestIdx)) {
                    bestIdx = idx;
                    bestKw = kw;
                }
            });
            if (bestIdx < 0) {
                el.appendChild(document.createTextNode(text.slice(i)));
                break;
            }
            if (bestIdx > i) {
                el.appendChild(document.createTextNode(text.slice(i, bestIdx)));
            }
            var mark = document.createElement("mark");
            mark.className = "ssi-hl";
            mark.textContent = text.slice(bestIdx, bestIdx + bestKw.length);
            el.appendChild(mark);
            i = bestIdx + bestKw.length;
        }
    }

    function emptyCard(msg) {
        list.innerHTML = "";
        var card = document.createElement("div");
        card.className = "stock-detail-card stock-detail-empty";
        var p = document.createElement("p");
        p.className = "empty";
        p.textContent = msg;
        card.appendChild(p);
        list.appendChild(card);
    }

    function detailRow(table, label, text, keywords) {
        var tr = document.createElement("tr");
        var th = document.createElement("th");
        th.textContent = label;
        var td = document.createElement("td");
        td.title = text || "";
        escText(td, text, keywords);
        tr.appendChild(th);
        tr.appendChild(td);
        table.appendChild(tr);
    }

    /** 行业字段行：带超链接跳转页面 G，展示该行业下全部股票 */
    function industryRow(table, label, text, level, keywords) {
        var tr = document.createElement("tr");
        var th = document.createElement("th");
        th.textContent = label;
        var td = document.createElement("td");
        var a = document.createElement("a");
        a.className = "industry-link";
        a.href = "/pageG?level=" + level + "&name=" + encodeURIComponent(text);
        a.title = "查看「" + text + "」行业下所有股票";
        escText(a, text, keywords);
        td.appendChild(a);
        tr.appendChild(th);
        tr.appendChild(td);
        table.appendChild(tr);
    }

    function renderCard(it, keywords) {
        var card = document.createElement("div");
        card.className = "stock-detail-card" + (it.nameHit ? " name-hit" : "");

        // 命中关键词徽章（右上角）
        (it.matched || []).forEach(function (kw) {
            var b = document.createElement("span");
            b.className = "batch-hit-badge card-hit";
            b.textContent = kw;
            card.appendChild(b);
        });

        // 两列布局（同页面 E）
        var cols = document.createElement("div");
        cols.className = "stock-detail-columns";

        // 第一列：股票名称、一/二/三级行业
        var col1 = document.createElement("div");
        col1.className = "stock-detail-col";
        var t1 = document.createElement("table");
        t1.className = "stock-detail-table";
        var tb1 = document.createElement("tbody");

        // 股票名称（纯文本展示）
        var nameTr = document.createElement("tr");
        var nameTh = document.createElement("th");
        nameTh.textContent = "股票名称";
        var nameTd = document.createElement("td");
        nameTd.className = "batch-name-text";
        escText(nameTd, it.name, keywords);
        nameTr.appendChild(nameTh);
        nameTr.appendChild(nameTd);
        tb1.appendChild(nameTr);

        industryRow(tb1, "一级行业", it.primary, 1, keywords);
        industryRow(tb1, "二级行业", it.secondary, 2, keywords);
        industryRow(tb1, "三级行业", it.tertiary, 3, keywords);        t1.appendChild(tb1);
        col1.appendChild(t1);
        cols.appendChild(col1);

        // 第二列：所在地、主营业务、概念 1、概念 2
        var col2 = document.createElement("div");
        col2.className = "stock-detail-col";
        var t2 = document.createElement("table");
        t2.className = "stock-detail-table";
        var tb2 = document.createElement("tbody");
        detailRow(tb2, "所在地", it.location, keywords);
        detailRow(tb2, "主营业务", it.main_business, keywords);
        detailRow(tb2, "概念 1", it.concept1, keywords);
        detailRow(tb2, "概念 2", it.concept2, keywords);
        t2.appendChild(tb2);
        col2.appendChild(t2);
        cols.appendChild(col2);

        card.appendChild(cols);
        return card;
    }

    function render(data) {
        lastKeywords = data.keywords || [];
        list.innerHTML = "";

        // 汇总信息
        summary.hidden = false;
        summary.innerHTML = "";
        var kwSpan = document.createElement("span");
        kwSpan.className = "batch-summary-kw";
        kwSpan.textContent = "关键词（" + lastKeywords.length + " 个）：" + lastKeywords.join("、");
        var cntSpan = document.createElement("span");
        cntSpan.className = "batch-summary-cnt";
        cntSpan.textContent = "命中 " + data.total + " 只股票" +
            (data.total > data.items.length ? "（仅展示前 " + data.items.length + " 条）" : "");
        summary.appendChild(kwSpan);
        summary.appendChild(cntSpan);

        if (!data.items.length) {
            emptyCard("没有匹配的股票");
            return;
        }

        data.items.forEach(function (it) {
            list.appendChild(renderCard(it, lastKeywords));
        });
    }

    function doSearch() {
        var q = input.value.trim();
        if (!q) {
            summary.hidden = true;
            emptyCard("请输入关键词");
            return;
        }
        btn.disabled = true;
        btn.textContent = "搜索中...";
        fetch("/api/stocks/search_batch", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ q: q }),
        })
            .then(function (r) { return r.json(); })
            .then(render)
            .catch(function () {
                summary.hidden = true;
                alert("搜索失败，请重试");
            })
            .finally(function () {
                btn.disabled = false;
                btn.textContent = "搜 索";
            });
    }

    btn.addEventListener("click", doSearch);
    input.addEventListener("keydown", function (e) {
        // 忽略输入法组合键
        if (e.isComposing || e.keyCode === 229) return;
        if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            doSearch();
        }
    });

    // 支持 URL 参数 ?q=... 进入时自动搜索（供页面 D 等跳转使用）
    var initQ = new URLSearchParams(window.location.search).get("q");
    if (initQ && initQ.trim()) {
        input.value = initQ.trim();
        doSearch();
    }
})();
