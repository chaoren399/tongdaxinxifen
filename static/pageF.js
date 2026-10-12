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

    /** 概念字段行：带超链接跳转页面 H，展示拥有该概念的所有股票 */
    function conceptRow(table, label, text, keywords) {
        var tr = document.createElement("tr");
        var th = document.createElement("th");
        th.textContent = label;
        var td = document.createElement("td");
        if (text) {
            var a = document.createElement("a");
            a.className = "industry-link";
            a.href = "/pageH?name=" + encodeURIComponent(text);
            a.title = "查看包含「" + text + "」概念的所有股票";
            escText(a, text, keywords);
            td.appendChild(a);
        } else {
            escText(td, text, keywords);
        }
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

    /** 多概念合并为一行展示（仅展示非空），每项仍是页面 H 的超链接 */
    function conceptGroupRow(table, label, texts, keywords) {
        var tr = document.createElement("tr");
        var th = document.createElement("th");
        th.textContent = label;
        var td = document.createElement("td");
        var items = (texts || []).filter(function (s) { return !!s; });
        if (!items.length) {
            escText(td, "—", keywords);
        } else {
            items.forEach(function (text, idx) {
                var a = document.createElement("a");
                a.className = "industry-link concept-chip";
                a.href = "/pageH?name=" + encodeURIComponent(text);
                a.title = "查看包含「" + text + "」概念的所有股票";
                escText(a, text, keywords);
                td.appendChild(a);
                if (idx < items.length - 1) {
                    td.appendChild(document.createTextNode(" "));
                }
            });
        }
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

        // 编辑按钮（卡片右上角）
        var editBtn = document.createElement("button");
        editBtn.type = "button";
        editBtn.className = "batch-edit-btn";
        editBtn.textContent = "编辑";
        editBtn.addEventListener("click", function () {
            openEditDialog(it, card);
        });
        card.appendChild(editBtn);

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

        // 股票代码
        var codeTr = document.createElement("tr");
        var codeTh = document.createElement("th");
        codeTh.textContent = "股票代码";
        var codeTd = document.createElement("td");
        escText(codeTd, it.code || "-", keywords);
        codeTr.appendChild(codeTh);
        codeTr.appendChild(codeTd);
        tb1.appendChild(codeTr);

        industryRow(tb1, "一级行业", it.primary, 1, keywords);
        industryRow(tb1, "二级行业", it.secondary, 2, keywords);
        industryRow(tb1, "三级行业", it.tertiary, 3, keywords);
        t1.appendChild(tb1);
        col1.appendChild(t1);
        cols.appendChild(col1);

        // 第二列：所在地、主营业务
        var col2 = document.createElement("div");
        col2.className = "stock-detail-col";
        var t2 = document.createElement("table");
        t2.className = "stock-detail-table";
        var tb2 = document.createElement("tbody");
        detailRow(tb2, "所在地", it.location, keywords);
        detailRow(tb2, "主营业务", it.main_business, keywords);
        // 概念：concept1~5 中 concept1 可能是用 - 等分隔符串接的多个概念，拆分合并去重后展示
        var conceptTexts = [];
        ["concept1", "concept2", "concept3", "concept4", "concept5"].forEach(function (k) {
            var raw = it[k] || "";
            if (!raw) return;
            raw.split(/[-，,、\s;；]+/).forEach(function (p) {
                var t = p.trim();
                if (t && conceptTexts.indexOf(t) === -1) conceptTexts.push(t);
            });
        });
        if (conceptTexts.length) {
            conceptGroupRow(tb2, "概念", conceptTexts, keywords);
        }
        t2.appendChild(tb2);
        col2.appendChild(t2);
        cols.appendChild(col2);

        card.appendChild(cols);
        return card;
    }

    function openEditDialog(it, card) {
        // 移除已有弹窗
        var exist = document.getElementById("batchEditModal");
        if (exist) exist.remove();

        // 可编辑字段：key => {label, value, maxlength}
        var fields = [
            { key: "name", label: "股票名称", value: it.name || "", maxlength: 30, required: true },
            { key: "code", label: "股票代码", value: it.code || "", maxlength: 10 },
            { key: "location", label: "所在地", value: it.location || "", maxlength: 40 },
            { key: "main_business", label: "主营业务", value: it.main_business || "", maxlength: 200 },
            { key: "concept1", label: "概念 1", value: it.concept1 || "", maxlength: 60 },
            { key: "concept2", label: "概念 2", value: it.concept2 || "", maxlength: 60 },
            { key: "concept3", label: "概念 3", value: it.concept3 || "", maxlength: 60 },
            { key: "concept4", label: "概念 4", value: it.concept4 || "", maxlength: 60 },
            { key: "concept5", label: "概念 5", value: it.concept5 || "", maxlength: 60 },
        ];

        var rows = "";
        fields.forEach(function (f) {
            var extra = f.required ? " required" : "";
            var ph = f.required ? ' placeholder="必填"' : "";
            rows +=
                '<div class="batch-modal-row">' +
                    '<label>' + escapeHtml(f.label) + '</label>' +
                    '<input class="batch-modal-input" data-key="' + f.key + '"' +
                        ' type="text" maxlength="' + f.maxlength + '"' +
                        ' value="' + escapeHtml(f.value) + '"' + ph + extra + ' />' +
                '</div>';
        });

        var modal = document.createElement("div");
        modal.id = "batchEditModal";
        modal.className = "batch-modal-mask";
        modal.innerHTML =
            '<div class="batch-modal batch-modal-wide">' +
                '<div class="batch-modal-title">编辑股票信息</div>' +
                '<div class="batch-modal-subtitle">所属：' + escapeHtml(it.primary) +
                    ' / ' + escapeHtml(it.secondary) +
                    ' / ' + escapeHtml(it.tertiary) + '</div>' +
                rows +
                '<div class="batch-modal-actions">' +
                    '<button class="batch-modal-cancel" type="button">取消</button>' +
                    '<button class="batch-modal-save" type="button">保存</button>' +
                '</div>' +
            '</div>';

        document.body.appendChild(modal);

        var inputs = modal.querySelectorAll(".batch-modal-input");
        inputs[0].focus();

        function close() {
            modal.remove();
            document.removeEventListener("keydown", onKey);
        }
        function onKey(e) {
            if (e.isComposing || e.keyCode === 229) return;
            if (e.key === "Escape") {
                e.preventDefault();
                close();
            } else if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
                e.preventDefault();
                save();
            }
        }
        function save() {
            var payload = { path: it.primary + "||" + it.secondary + "||" + it.tertiary, name: it.name, fields: {} };
            inputs.forEach(function (inp) {
                payload.fields[inp.getAttribute("data-key")] = inp.value.trim();
            });
            if (!payload.fields.name) {
                alert("股票名称不能为空");
                return;
            }
            var saveBtn = modal.querySelector(".batch-modal-save");
            saveBtn.disabled = true;
            saveBtn.textContent = "保存中...";
            fetch("/api/stock/update_concepts", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload),
            })
                .then(function (r) { return r.json(); })
                .then(function (data) {
                    if (data.ok) {
                        // 用后端返回的最新值更新当前对象，再重渲染该卡片
                        Object.keys(data.stock).forEach(function (k) {
                            it[k] = data.stock[k];
                        });
                        var newCard = renderCard(it, lastKeywords);
                        card.replaceWith(newCard);
                        close();
                    } else {
                        alert("保存失败：" + (data.error || ""));
                        saveBtn.disabled = false;
                        saveBtn.textContent = "保存";
                    }
                })
                .catch(function () {
                    alert("网络错误，请重试");
                    saveBtn.disabled = false;
                    saveBtn.textContent = "保存";
                });
        }

        modal.querySelector(".batch-modal-cancel").addEventListener("click", close);
        modal.querySelector(".batch-modal-save").addEventListener("click", save);
        modal.addEventListener("click", function (e) {
            if (e.target === modal) close();
        });
        document.addEventListener("keydown", onKey);
    }

    function escapeHtml(s) {
        return String(s || "").replace(/[&<>"']/g, function (c) {
            return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
        });
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
