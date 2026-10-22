/* 页面 F：多股票批量搜索，结果按页面 E 卡片格式展示 */
(function () {
    "use strict";

    var input = document.getElementById("batchSearchInput");
    var btn = document.getElementById("batchSearchBtn");
    var list = document.getElementById("batchResultList");
    var historyRow = document.getElementById("searchHistoryRow");
    var historyChips = document.getElementById("searchHistoryChips");
    var lastKeywords = [];
    // 固定高亮词「主营」：命中时加粗 + 加深（黑色）
    var MAIN_BIZ_KEYWORD = "主营";

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
            mark.className = bestKw === MAIN_BIZ_KEYWORD
                ? "ssi-hl ssi-hl-main"
                : "ssi-hl";
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

    /** 卡片式字段项：左侧蓝色竖条 + 蓝色标签 + 内容（如 A: xxx） */
    function cardFieldItem(container, label, text, keywords) {
        var row = document.createElement("div");
        row.className = "stock-detail-card-item";

        var left = document.createElement("span");
        left.className = "stock-detail-card-label";
        left.textContent = label + ":";

        var value = document.createElement("span");
        value.className = "stock-detail-card-value";
        value.title = text || "";
        escText(value, text, keywords);

        row.appendChild(left);
        row.appendChild(value);
        container.appendChild(row);
    }

    /** 多概念合并为一行展示，每项仍是页面 H 的超链接；空时显示 — */
    function conceptGroupRow(table, label, texts, keywords) {
        var tr = document.createElement("tr");
        var th = document.createElement("th");
        th.textContent = label;
        var td = document.createElement("td");
        var items = (texts || []).filter(function (s) { return !!s; });
        if (items.length === 0) {
            td.textContent = "—";
            tr.appendChild(th);
            tr.appendChild(td);
            table.appendChild(tr);
            return;
        }
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

    /** 取一只股票的所有图片 URL（兼容旧的单图字段） */
    function cardImageUrls(it) {
        var arr = it && it.images;
        if (Object.prototype.toString.call(arr) === "[object Array]" && arr.length) {
            return arr.map(function (x) {
                return x.url || ("/stock_images/" + x.filename);
            });
        }
        return (it && it.image_url) ? [it.image_url] : [];
    }

    /**
     * 点击缩略图后的大图查看层，支持在当前股票的所有图片间翻看。
     * @param {string[]} urls  当前股票的全部图片 URL
     * @param {number}   startIdx  初始显示的索引
     * @param {string}   [alt]     alt 文本
     */
    function openLightbox(urls, startIdx, alt) {
        var exist = document.getElementById("stockImageLightbox");
        if (exist) exist.remove();
        if (!urls || !urls.length) return;
        var idx = Math.max(0, Math.min(startIdx || 0, urls.length - 1));

        var box = document.createElement("div");
        box.id = "stockImageLightbox";
        box.className = "stock-lightbox";

        var img = document.createElement("img");
        img.alt = alt || "";
        img.className = "stock-lightbox-img";
        img.addEventListener("click", function (e) { e.stopPropagation(); });

        var prevBtn = document.createElement("button");
        prevBtn.type = "button";
        prevBtn.className = "stock-lightbox-nav stock-lightbox-prev";
        prevBtn.textContent = "‹";
        prevBtn.title = "上一张 (←)";
        prevBtn.addEventListener("click", function (e) {
            e.stopPropagation();
            navigate(-1);
        });

        var nextBtn = document.createElement("button");
        nextBtn.type = "button";
        nextBtn.className = "stock-lightbox-nav stock-lightbox-next";
        nextBtn.textContent = "›";
        nextBtn.title = "下一张 (→)";
        nextBtn.addEventListener("click", function (e) {
            e.stopPropagation();
            navigate(1);
        });

        var counter = document.createElement("div");
        counter.className = "stock-lightbox-counter";

        var closeBtn = document.createElement("button");
        closeBtn.type = "button";
        closeBtn.className = "stock-lightbox-close";
        closeBtn.textContent = "×";
        closeBtn.title = "关闭 (Esc)";
        closeBtn.addEventListener("click", function (e) {
            e.stopPropagation();
            close();
        });

        box.appendChild(prevBtn);
        box.appendChild(img);
        box.appendChild(nextBtn);
        box.appendChild(counter);
        box.appendChild(closeBtn);
        box.addEventListener("click", close);
        document.body.appendChild(box);

        function show() {
            img.src = urls[idx];
            counter.textContent = (idx + 1) + " / " + urls.length;
            var multi = urls.length > 1;
            prevBtn.style.display = multi ? "" : "none";
            nextBtn.style.display = multi ? "" : "none";
        }
        function navigate(delta) {
            idx = (idx + delta + urls.length) % urls.length;
            show();
        }
        function close() {
            box.remove();
            document.removeEventListener("keydown", onKey);
        }
        function onKey(e) {
            if (e.key === "Escape") { e.preventDefault(); close(); }
            else if (e.key === "ArrowLeft" && urls.length > 1) {
                e.preventDefault(); navigate(-1);
            } else if (e.key === "ArrowRight" && urls.length > 1) {
                e.preventDefault(); navigate(1);
            }
        }
        document.addEventListener("keydown", onKey);
        show();
    }

    function renderCard(it, keywords) {
        var card = document.createElement("div");
        card.className = "stock-detail-card" + (it.nameHit ? " name-hit" : "");

        // 命中关键词 / 模糊匹配徽章（右上角，横向排列避免重叠）
        var badges = document.createElement("div");
        badges.className = "card-badges";
        (it.matched || []).forEach(function (kw) {
            var b = document.createElement("span");
            b.className = "batch-hit-badge card-hit";
            b.textContent = kw;
            badges.appendChild(b);
        });
        if (it.fuzzy) {
            var fb = document.createElement("span");
            fb.className = "batch-hit-badge card-fuzzy";
            fb.textContent = "模糊";
            badges.appendChild(fb);
        }
        card.appendChild(badges);

        // 编辑按钮（卡片右上角）
        var editBtn = document.createElement("button");
        editBtn.type = "button";
        editBtn.className = "batch-edit-btn";
        editBtn.textContent = "编辑";
        editBtn.addEventListener("click", function () {
            openEditDialog(it, card);
        });
        card.appendChild(editBtn);

        // 头部信息行：股票名称 · 代码 · 一级 / 二级 / 三级行业
        var header = document.createElement("div");
        header.className = "stock-detail-header";
        var nameSpan = document.createElement("span");
        nameSpan.className = "stock-detail-name";
        nameSpan.setAttribute("data-stock", it.name);
        escText(nameSpan, it.name, keywords);
        header.appendChild(nameSpan);

        if (it.code) {
            var codeSpan = document.createElement("span");
            codeSpan.className = "stock-detail-code";
            codeSpan.textContent = it.code;
            header.appendChild(codeSpan);
        }

        function industryTag(text, level) {
            var a = document.createElement("a");
            a.className = "stock-detail-industry";
            // hl：来源股票名，页面 G 会高亮定位该股票
            a.href = "/pageG?level=" + level + "&name=" + encodeURIComponent(text)
                + "&hl=" + encodeURIComponent(it.name || "");
            a.title = "查看「" + text + "」行业下所有股票";
            a.textContent = text;
            return a;
        }
        if (it.primary) {
            var sep1 = document.createElement("span");
            sep1.className = "stock-detail-sep";
            sep1.textContent = "›";
            header.appendChild(sep1);
            header.appendChild(industryTag(it.primary, 1));
        }
        if (it.secondary) {
            var sep2 = document.createElement("span");
            sep2.className = "stock-detail-sep";
            sep2.textContent = "›";
            header.appendChild(sep2);
            header.appendChild(industryTag(it.secondary, 2));
        }
        if (it.tertiary) {
            var sep3 = document.createElement("span");
            sep3.className = "stock-detail-sep";
            sep3.textContent = "›";
            header.appendChild(sep3);
            header.appendChild(industryTag(it.tertiary, 3));
        }
        card.appendChild(header);

        // 三列布局：左侧概念+选项，中部图片，右侧修改历史
        var cols = document.createElement("div");
        cols.className = "stock-detail-columns";

        // 计算概念文本（拆分合并去重），后续概念独立列会用到。
        // 概念串中可能含完整路径 "一级/二级/..."，这里只取一级概念展示。
        var conceptTexts = [];
        ["concept1", "concept2", "concept3", "concept4", "concept5"].forEach(function (k) {
            var raw = it[k] || "";
            if (!raw) return;
            raw.split(/[-，,、\s;；]+/).forEach(function (p) {
                var root = p.split("/")[0].trim();
                if (root && conceptTexts.indexOf(root) === -1) conceptTexts.push(root);
            });
        });

        // 第一列：概念（独立列，最左侧）
        var col5 = document.createElement("div");
        col5.className = "stock-detail-col stock-detail-concept-col";
        var conceptTitle = document.createElement("div");
        conceptTitle.className = "stock-detail-concept-col-title";
        conceptTitle.textContent = "概念";
        col5.appendChild(conceptTitle);
        var conceptList = document.createElement("div");
        conceptList.className = "stock-detail-concept-col-list";
        if (conceptTexts.length) {
            conceptTexts.forEach(function (txt) {
                // 概念保持原有的"点击跳转 pageH"超链接行为
                var a = document.createElement("a");
                a.className = "stock-detail-concept-chip";
                // hl：来源股票名，页面 H 会高亮并滚动定位到该股票
                a.href = "/pageH?name=" + encodeURIComponent(txt)
                    + "&hl=" + encodeURIComponent(it.name || "");
                a.title = "查看包含「" + txt + "」概念的所有股票";
                escText(a, txt, keywords);
                conceptList.appendChild(a);
            });
        } else {
            var conceptEmpty = document.createElement("span");
            conceptEmpty.className = "stock-detail-history-empty";
            conceptEmpty.textContent = "—";
            conceptList.appendChild(conceptEmpty);
        }
        col5.appendChild(conceptList);
        cols.appendChild(col5);

        // 左侧列：选项A~H（概念已挪到 col5，不再在表格里）
        var col2 = document.createElement("div");
        col2.className = "stock-detail-col";
        // 选项A ~ 选项H：改成卡片式字段（左侧蓝色竖线 + 蓝色标签 A: + 内容）
        var OPTION_FIELDS = [
            { key: "option_a", label: "A" },
            { key: "option_b", label: "B" },
            { key: "option_c", label: "C" },
            { key: "option_d", label: "D" },
            { key: "option_e", label: "E" },
            { key: "option_f", label: "F" },
            { key: "option_g", label: "G" },
            { key: "option_h", label: "H" },
        ];
        var optionsBox = document.createElement("div");
        optionsBox.className = "stock-detail-options";
        OPTION_FIELDS.forEach(function (f) {
            var v = (it[f.key] || "").trim();
            if (!v) return; // 空值不显示该选项
            cardFieldItem(optionsBox, f.label, v, keywords);
        });
        col2.appendChild(optionsBox);

        // ===== 概念业务描述：放在中列最下边（选项 A~H 之后），支持内联编辑/新增 =====
        // 候选概念：来自股票的 concept1~5（可能是一级/二级/... 的完整路径），
        // 取每段路径的最后一级作为概念名（对应 DB 中的 concepts.name）。
        var bizCandidates = [];
        ["concept1", "concept2", "concept3", "concept4", "concept5"].forEach(function (k) {
            var raw = it[k] || "";
            if (!raw) return;
            raw.split(/[-，,、\s;；]+/).forEach(function (p) {
                var segs = p.split("/")
                    .map(function (s) { return s.trim(); })
                    .filter(function (s) { return !!s; });
                if (!segs.length) return;
                var leaf = segs[segs.length - 1];
                var dup = bizCandidates.some(function (c) { return c.name === leaf; });
                if (!dup) bizCandidates.push({ name: leaf, path: segs.join("/") });
            });
        });

        var bizBox = document.createElement("div");
        bizBox.className = "stock-detail-concept-biz-list";

        function bizList() {
            return (it.businesses || []).filter(function (b) { return b && b.business; });
        }

        /** 保存「股票 × 概念」业务描述；business 为空串即清除 */
        function saveBiz(biz, text, done) {
            fetch("/api/pageK/stock_business", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ concept: biz.concept, stock: it.name, business: text }),
            })
                .then(function (r) { return r.json(); })
                .then(function (data) {
                    if (!data || !data.ok) {
                        alert("保存失败：" + ((data && data.error) || "未知错误"));
                        return;
                    }
                    var saved = (data.business || "").trim();
                    var arr = (it.businesses || []).slice();
                    var hit = null;
                    arr.forEach(function (b) { if (b && b.concept === biz.concept) hit = b; });
                    if (!saved) {
                        arr = arr.filter(function (b) { return !(b && b.concept === biz.concept); });
                    } else if (hit) {
                        hit.business = saved;
                    } else {
                        arr.push({ concept: biz.concept, path: biz.path || biz.concept, business: saved });
                    }
                    it.businesses = arr;
                    if (done) done();
                    renderBizBox();
                })
                .catch(function (err) {
                    alert("保存失败：" + ((err && err.message) || "网络错误，请重试"));
                    if (done) done();
                });
        }

        /** 把某一行替换为内联编辑器 */
        function buildBizEditor(row, biz) {
            row.innerHTML = "";
            row.className = "stock-detail-concept-biz-row is-editing";

            var tip = document.createElement("span");
            tip.className = "stock-detail-biz-tip";
            tip.textContent = (biz.path || biz.concept || "");

            var input = document.createElement("input");
            input.type = "text";
            input.className = "stock-detail-biz-input";
            input.value = biz.business || "";
            input.maxLength = 200;
            input.placeholder = "业务描述（留空保存即清除）";

            var btnSave = document.createElement("button");
            btnSave.type = "button";
            btnSave.className = "stock-detail-biz-btn is-save";
            btnSave.textContent = "保存";

            var btnCancel = document.createElement("button");
            btnCancel.type = "button";
            btnCancel.className = "stock-detail-biz-btn";
            btnCancel.textContent = "取消";

            btnSave.addEventListener("click", function () {
                btnSave.disabled = true;
                saveBiz(biz, input.value.trim(), function () { btnSave.disabled = false; });
            });
            btnCancel.addEventListener("click", renderBizBox);
            input.addEventListener("keydown", function (e) {
                if (e.isComposing || e.keyCode === 229) return;
                if (e.key === "Enter") {
                    e.preventDefault();
                    btnSave.click();
                } else if (e.key === "Escape") {
                    e.preventDefault();
                    renderBizBox();
                }
            });

            row.appendChild(tip);
            row.appendChild(input);
            row.appendChild(btnSave);
            row.appendChild(btnCancel);
            input.focus();
        }

        function renderBizBox() {
            bizBox.innerHTML = "";
            bizList().forEach(function (biz) {
                var row = document.createElement("div");
                row.className = "stock-detail-concept-biz-row";
                var path = biz.path || biz.concept || "";
                var line = document.createElement("span");
                line.className = "stock-detail-concept-biz";
                line.title = path;
                line.textContent = path + "：" + biz.business;
                var editBtn = document.createElement("button");
                editBtn.type = "button";
                editBtn.className = "stock-detail-biz-edit";
                editBtn.textContent = "✎";
                editBtn.title = "编辑「" + path + "」的业务描述";
                editBtn.addEventListener("click", function () { buildBizEditor(row, biz); });
                row.appendChild(line);
                row.appendChild(editBtn);
                bizBox.appendChild(row);
            });

            // 还没有描述的概念：可新增
            var used = {};
            bizList().forEach(function (b) { used[b.concept] = true; });
            var addable = bizCandidates.filter(function (c) { return !used[c.name]; });
            if (addable.length) {
                var addRow = document.createElement("div");
                addRow.className = "stock-detail-concept-biz-row is-add";
                var sel = document.createElement("select");
                sel.className = "stock-detail-biz-select";
                addable.forEach(function (c) {
                    var op = document.createElement("option");
                    op.value = c.name;
                    op.textContent = c.path;
                    sel.appendChild(op);
                });
                var addBtn = document.createElement("button");
                addBtn.type = "button";
                addBtn.className = "stock-detail-biz-btn is-add";
                addBtn.textContent = "＋ 添加描述";
                addBtn.addEventListener("click", function () {
                    var chosen = null;
                    addable.forEach(function (c) { if (c.name === sel.value) chosen = c; });
                    if (!chosen) return;
                    buildBizEditor(addRow, {
                        concept: chosen.name,
                        path: chosen.path,
                        business: "",
                    });
                });
                addRow.appendChild(sel);
                addRow.appendChild(addBtn);
                bizBox.appendChild(addRow);
            }
            bizBox.hidden = !bizBox.childNodes.length;
        }

        if (bizCandidates.length || bizList().length) {
            renderBizBox();
            col2.appendChild(bizBox);
        }

        cols.appendChild(col2);

        // 第三列：图片（只读预览，支持多张；编辑入口在「编辑」弹窗里）
        var col3 = document.createElement("div");
        col3.className = "stock-detail-col stock-detail-image-col";
        var urls = cardImageUrls(it);
        if (urls.length) {
            var grid = document.createElement("div");
            grid.className = "stock-image-grid";
            // 默认只渲染第 1 张：多图时右下角叠 "+N" 角标，点击进入灯箱
            var cell = document.createElement("div");
            cell.className = "stock-image-cell";
            var img = document.createElement("img");
            img.src = urls[0];
            img.alt = it.name;
            img.className = "stock-image";
            img.loading = "lazy";
            cell.appendChild(img);
            if (urls.length > 1) {
                var more = document.createElement("button");
                more.type = "button";
                more.className = "stock-image-more";
                more.textContent = "+" + (urls.length - 1);
                more.title = "还有 " + (urls.length - 1) + " 张图片，点击查看全部";
                cell.appendChild(more);
            }
            cell.addEventListener("click", function () {
                openLightbox(urls, 0, it.name);
            });
            grid.appendChild(cell);
            col3.appendChild(grid);
        } else {
            var imgWrap = document.createElement("div");
            imgWrap.className = "stock-image-wrap";
            col3.appendChild(imgWrap);
        }
        cols.appendChild(col3);

        // 第三列：修改历史（独立列）
        var col4 = document.createElement("div");
        col4.className = "stock-detail-col stock-detail-history-col";
        var histTitle = document.createElement("div");
        histTitle.className = "stock-detail-history-col-title";
        histTitle.textContent = "修改历史";
        col4.appendChild(histTitle);
        var histList = document.createElement("div");
        histList.className = "stock-detail-history-col-list";
        var historyDates = Array.isArray(it.modification_dates)
            ? it.modification_dates
            : [];
        if (historyDates.length === 0) {
            var empty = document.createElement("span");
            empty.className = "stock-detail-history-empty";
            empty.textContent = "—";
            histList.appendChild(empty);
        } else {
            historyDates.forEach(function (d, idx) {
                var chip = document.createElement("span");
                chip.className = "stock-detail-history-date";
                chip.textContent = d;
                // 高亮第一个（最新一次）日期
                if (idx === 0) chip.classList.add("is-latest");
                histList.appendChild(chip);
            });
        }
        col4.appendChild(histList);
        cols.appendChild(col4);

        card.appendChild(cols);
        return card;
    }

    function openEditDialog(it, card) {
        // 移除已有弹窗
        var exist = document.getElementById("batchEditModal");
        if (exist) exist.remove();

        // 拉取一级概念列表
        fetch("/api/concepts/primary")
            .then(function (r) { return r.json(); })
            .then(function (data) {
                var primaryConcepts = (data && data.ok) ? (data.items || []) : [];
                buildAndShowModal(primaryConcepts);
            })
            .catch(function () {
                buildAndShowModal([]);
            });

        function buildAndShowModal(primaryConcepts) {
            // 可编辑字段：key => {label, value, maxlength}
            var fields = [
                { key: "name", label: "股票名称", value: it.name || "", maxlength: 30, required: true },
                { key: "code", label: "股票代码", value: it.code || "", maxlength: 10 },
            ];

            // 概念 1~5：从一级概念下拉选择；旧数据可能是 "固态电池-材料X" 这种拼接串，
            // 这里把它拆成单个一级概念，按顺序填入 concept1~5；不是一级概念的片段不展示也不保存。
            var primaryNames = {};
            primaryConcepts.forEach(function (pc) { primaryNames[pc.name] = true; });
            var conceptPieces = [];
            ["concept1", "concept2", "concept3", "concept4", "concept5"].forEach(function (k) {
                var raw = it[k] || "";
                if (!raw) return;
                raw.split(/[-，,、\s;；]+/).forEach(function (p) {
                    // 完整路径如 "固态电池/材料/..." → 只把一级送进下拉
                    var root = p.split("/")[0].trim();
                    if (root && primaryNames[root] && conceptPieces.indexOf(root) === -1) {
                        conceptPieces.push(root);
                    }
                });
            });
            var conceptFields = [
                { key: "concept1", label: "概念 1", value: conceptPieces[0] || "" },
                { key: "concept2", label: "概念 2", value: conceptPieces[1] || "" },
                { key: "concept3", label: "概念 3", value: conceptPieces[2] || "" },
                { key: "concept4", label: "概念 4", value: conceptPieces[3] || "" },
                { key: "concept5", label: "概念 5", value: conceptPieces[4] || "" },
            ];

            var optionFields = [
                { key: "option_a", label: "选项A", value: it.option_a || "", maxlength: 60 },
                { key: "option_b", label: "选项B", value: it.option_b || "", maxlength: 60 },
                { key: "option_c", label: "选项C", value: it.option_c || "", maxlength: 60 },
                { key: "option_d", label: "选项D", value: it.option_d || "", maxlength: 60 },
                { key: "option_e", label: "选项E", value: it.option_e || "", maxlength: 60 },
                { key: "option_f", label: "选项F", value: it.option_f || "", maxlength: 60 },
                { key: "option_g", label: "选项G", value: it.option_g || "", maxlength: 60 },
                { key: "option_h", label: "选项H", value: it.option_h || "", maxlength: 60 },
            ];

            function buildRows(list) {
                return list.map(function (f) {
                    var extra = f.required ? " required" : "";
                    var ph = f.required ? ' placeholder="必填"' : "";
                    return '<div class="batch-modal-row">' +
                        '<label>' + escapeHtml(f.label) + '</label>' +
                        '<input class="batch-modal-input" data-key="' + f.key + '"' +
                            ' type="text" maxlength="' + f.maxlength + '"' +
                            ' value="' + escapeHtml(f.value) + '"' + ph + extra + ' />' +
                    '</div>';
                }).join("");
            }

            function buildConceptSelects(list) {
                return list.map(function (f) {
                    var opts = ['<option value="">（空）</option>'];
                    var hasCurrent = false;
                    primaryConcepts.forEach(function (pc) {
                        var name = pc.name || "";
                        if (name === f.value) hasCurrent = true;
                        var usage = pc.usage ? " (" + pc.usage + ")" : "";
                        var label = name + usage;
                        opts.push(
                            '<option value="' + escapeHtml(name) + '"' +
                            (name === f.value ? " selected" : "") + ">" +
                            escapeHtml(label) + "</option>"
                        );
                    });
                    return '<div class="batch-modal-row">' +
                        '<label>' + escapeHtml(f.label) + '</label>' +
                        '<select class="batch-modal-input" data-key="' + f.key + '">' +
                            opts.join("") +
                        '</select>' +
                    '</div>';
                }).join("");
            }

            var rows = buildRows(fields) +
                '<div class="batch-modal-section-title">选项 A ~ H</div>' +
                buildRows(optionFields) +
                '<div class="batch-modal-section-title">图片（可上传多张）</div>' +
                '<div class="batch-modal-images">' +
                    '<div class="batch-modal-image-grid" id="modalImageGrid"></div>' +
                    '<div class="batch-modal-image-actions">' +
                        '<input type="file" accept="image/*" id="modalImageInput" multiple hidden />' +
                        '<button type="button" class="batch-btn" id="modalUploadBtn">添加图片</button>' +
                        '<span class="batch-modal-image-hint">可多选，或直接按 Ctrl+V 粘贴图片</span>' +
                    '</div>' +
                '</div>' +
                '<div class="batch-modal-section-title">概念（从一级概念中选择）</div>' +
                buildConceptSelects(conceptFields);

            var modal = document.createElement("div");
            modal.id = "batchEditModal";
            modal.className = "batch-modal-mask";
            modal.innerHTML =
                '<div class="batch-modal batch-modal-wide">' +
                    '<div class="batch-modal-title">编辑股票信息</div>' +
                    '<div class="batch-modal-subtitle">所属：' + escapeHtml(it.primary) +
                        ' / ' + escapeHtml(it.secondary) +
                        ' / ' + escapeHtml(it.tertiary) + '</div>' +
                    '<div class="batch-modal-body">' + rows + '</div>' +
                    '<div class="batch-modal-actions">' +
                        '<button class="batch-modal-cancel" type="button">取消</button>' +
                        '<button class="batch-modal-save" type="button">保存</button>' +
                    '</div>' +
                '</div>';

            document.body.appendChild(modal);

            var inputs = modal.querySelectorAll(".batch-modal-input");
            inputs[0].focus();

            function close() {
                // 释放暂存图片的本地预览 URL（未保存的改动直接丢弃）
                pendingUploads.forEach(function (p) { URL.revokeObjectURL(p.url); });
                pendingUploads = [];
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

                function fail(msg) {
                    alert("保存失败：" + msg);
                    saveBtn.disabled = false;
                    saveBtn.textContent = "保存";
                }

                // 统一提交：先处理图片（删除 -> 上传），全部成功后再提交字段
                var seq = Promise.resolve();
                pendingDeletes.forEach(function (fn) {
                    seq = seq.then(function () { return apiDeleteImage(fn); });
                });
                pendingUploads.forEach(function (p) {
                    seq = seq.then(function () { return apiUploadImage(p.file); });
                });

                seq
                    .then(function () {
                        return fetch("/api/stock/update_concepts", {
                            method: "POST",
                            headers: { "Content-Type": "application/json" },
                            body: JSON.stringify(payload),
                        }).then(function (r) { return r.json(); });
                    })
                    .then(function (data) {
                        if (!data.ok) { fail(data.error || ""); return; }
                        // 用后端返回的最新字段值更新当前对象
                        Object.keys(data.stock).forEach(function (k) {
                            it[k] = data.stock[k];
                        });
                        // 图片以服务器最终结果为准
                        if (serverImages !== null) {
                            it.images = serverImages;
                            it.image_path = serverImages.length ? serverImages[0].filename : "";
                            it.image_url = serverImages.length ? serverImages[0].url : "";
                        }
                        var newCard = renderCard(it, lastKeywords);
                        card.replaceWith(newCard);
                        close();
                    })
                    .catch(function (err) {
                        fail((err && err.message) || "网络错误，请重试");
                    });
            }

            modal.querySelector(".batch-modal-cancel").addEventListener("click", close);
            modal.querySelector(".batch-modal-save").addEventListener("click", save);
            modal.addEventListener("click", function (e) {
                if (e.target === modal) close();
            });

            // ===== 图片：编辑期间仅暂存，点击「保存」后才真正提交 =====
            var imgInput = modal.querySelector("#modalImageInput");
            var imgGrid = modal.querySelector("#modalImageGrid");
            var uploadBtn = modal.querySelector("#modalUploadBtn");

            // 本次编辑开始时，服务器上已有的图片（快照）
            var baseImages = cardImageUrls(it);
            var pendingDeletes = [];   // 待删除的已存在图片文件名
            var pendingUploads = [];   // 待上传的新图片 [{file, url}]
            var serverImages = null;   // 保存流程中服务器返回的最新图片列表

            function imageFilename(url) {
                return url.split("?")[0].split("/").pop();
            }

            function makeImageItem(url, alt, onRemove) {
                var cell = document.createElement("div");
                cell.className = "batch-modal-image-item";
                var img = document.createElement("img");
                img.src = url;
                img.alt = alt || "";
                var del = document.createElement("button");
                del.type = "button";
                del.className = "batch-modal-image-del";
                del.title = "移除这张图片";
                del.textContent = "×";
                del.addEventListener("click", onRemove);
                cell.appendChild(img);
                cell.appendChild(del);
                return cell;
            }

            function renderImageGrid() {
                imgGrid.innerHTML = "";
                var shown = 0;
                // 已存在图片（排除本次标记删除的）
                baseImages.forEach(function (u) {
                    var fn = imageFilename(u);
                    if (pendingDeletes.indexOf(fn) !== -1) return;
                    shown++;
                    imgGrid.appendChild(makeImageItem(u, it.name, function () {
                        if (pendingDeletes.indexOf(fn) === -1) pendingDeletes.push(fn);
                        renderImageGrid();
                    }));
                });
                // 暂存的新图片（本地预览）
                pendingUploads.forEach(function (p) {
                    shown++;
                    imgGrid.appendChild(makeImageItem(p.url, "待保存的新图片", function () {
                        var i = pendingUploads.indexOf(p);
                        if (i !== -1) {
                            URL.revokeObjectURL(p.url);
                            pendingUploads.splice(i, 1);
                        }
                        renderImageGrid();
                    }));
                });
                if (!shown) {
                    var empty = document.createElement("span");
                    empty.className = "batch-modal-image-empty";
                    empty.textContent = "暂无图片";
                    imgGrid.appendChild(empty);
                }
            }
            renderImageGrid();

            // 把选择的文件暂存为本地预览（不提交）
            function stageFiles(files) {
                Array.prototype.slice.call(files || []).forEach(function (f) {
                    if (!f) return;
                    pendingUploads.push({ file: f, url: URL.createObjectURL(f) });
                });
                imgInput.value = "";
                renderImageGrid();
            }

            uploadBtn.addEventListener("click", function () { imgInput.click(); });
            imgInput.addEventListener("change", function () { stageFiles(imgInput.files); });

            // 真正提交：删除一张已存在图片（保存时调用）
            function apiDeleteImage(filename) {
                return fetch("/api/stock/delete_image", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        path: it.primary + "||" + it.secondary + "||" + it.tertiary,
                        name: it.name,
                        filename: filename,
                    }),
                })
                    .then(function (r) { return r.json(); })
                    .then(function (data) {
                        if (!data.ok) throw new Error(data.error || "删除图片失败");
                        serverImages = data.images || [];
                    });
            }

            // 真正提交：上传一张新图片（保存时调用）
            function apiUploadImage(file) {
                var fd = new FormData();
                fd.append("path", it.primary + "||" + it.secondary + "||" + it.tertiary);
                fd.append("name", it.name);
                fd.append("file", file);
                return fetch("/api/stock/upload_image", { method: "POST", body: fd })
                    .then(function (r) { return r.json(); })
                    .then(function (data) {
                        if (!data.ok) throw new Error(data.error || "上传图片失败");
                        serverImages = data.images || [];
                    });
            }

            // 支持在编辑弹窗内 Ctrl+V 粘贴图片（暂存，保存时才上传）
            modal.addEventListener("paste", function (e) {
                var items = (e.clipboardData && e.clipboardData.items) || [];
                for (var i = 0; i < items.length; i++) {
                    var item = items[i];
                    if (item.kind === "file" && item.type && item.type.indexOf("image") === 0) {
                        var blob = item.getAsFile();
                        if (!blob) continue;
                        e.preventDefault();
                        var sub = (item.type.split("/")[1] || "png").replace("jpeg", "jpg");
                        var file = new File([blob], "pasted_" + Date.now() + "." + sub,
                            { type: item.type });
                        stageFiles([file]);
                        break;
                    }
                }
            });
            document.addEventListener("keydown", onKey);
        }
    }

    function escapeHtml(s) {
        return String(s || "").replace(/[&<>"']/g, function (c) {
            return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
        });
    }

    function render(data) {
        // 搜索关键词 + 固定高亮词「主营」：只要文本里出现「主营」就高亮
        lastKeywords = (data.keywords || []).slice();
        if (lastKeywords.indexOf(MAIN_BIZ_KEYWORD) < 0) {
            lastKeywords.push(MAIN_BIZ_KEYWORD);
        }
        list.innerHTML = "";

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
            // 联想面板打开且有高亮项：先补全成完整股票名称再搜索
            if (suggestBox && !suggestBox.hidden && suggestIndex >= 0 && suggestItems[suggestIndex]) {
                applySuggest(suggestIndex);
            }
            doSearch();
        }
    });

    // ===== 搜索框股票名称联想（同页面 K 批量添加股票的提示） =====
    var suggestBox = document.getElementById("batchSuggest");
    var suggestItems = [];   // 当前候选 [{name, code}]
    var suggestIndex = -1;   // 高亮项下标
    var allStocks = null;    // 全量股票缓存
    var suggestTimer = null;

    function loadAllStocks() {
        if (allStocks) return;
        fetch("/api/pageK/all_stocks")
            .then(function (r) { return r.json(); })
            .then(function (j) {
                if (!j.ok) return;
                allStocks = j.stocks || [];
            })
            .catch(function () { /* 静默失败：联想不可用不影响正常搜索 */ });
    }

    function matchStocks(term, limit) {
        // 名称匹配优先于代码匹配，开头匹配优先于包含匹配
        var kw = (term || "").trim();
        if (!kw || !allStocks) return [];
        var lower = kw.toLowerCase();
        var starts = [];
        var contains = [];
        for (var i = 0; i < allStocks.length; i++) {
            var s = allStocks[i];
            var name = s.name || "";
            var code = (s.code || "").toString();
            var ni = name.indexOf(kw);
            var ci = code.toLowerCase().indexOf(lower);
            if (ni === 0) starts.push(s);
            else if (ci === 0 && code) starts.push(s);
            else if (ni > 0) contains.push(s);
            else if (ci > 0) contains.push(s);
            if (starts.length >= limit) break;
        }
        return starts.concat(contains).slice(0, limit);
    }

    function hideSuggest() {
        if (!suggestBox) return;
        suggestBox.hidden = true;
        suggestItems = [];
        suggestIndex = -1;
    }

    function setSuggestIndex(idx) {
        suggestIndex = idx;
        var rows = suggestBox.querySelectorAll(".pf-suggest-item");
        for (var i = 0; i < rows.length; i++) {
            if (i === idx) rows[i].classList.add("is-active");
            else rows[i].classList.remove("is-active");
        }
        var act = suggestBox.querySelector(".pf-suggest-item.is-active");
        if (act && act.scrollIntoView) act.scrollIntoView({ block: "nearest" });
    }

    function renderSuggest(term) {
        if (!suggestBox) return;
        suggestBox.innerHTML = "";
        var list2 = matchStocks(term, 8);
        suggestItems = list2.map(function (s) {
            return {
                name: s.name || "",
                code: s.code || "",
                optionB: (s.option_b || "").trim(),
                optionD: (s.option_d || "").trim()
            };
        });
        suggestIndex = suggestItems.length ? 0 : -1;

        if (!suggestItems.length) {
            var empty = document.createElement("div");
            empty.className = "pf-suggest-empty";
            empty.textContent = "没有匹配的股票";
            suggestBox.appendChild(empty);
            suggestBox.hidden = false;
            return;
        }

        suggestItems.forEach(function (item, idx) {
            var row = document.createElement("div");
            row.className = "pf-suggest-item";
            if (idx === suggestIndex) row.classList.add("is-active");

            // 高亮命中片段（用 DOM 拼接，避免 innerHTML 注入）
            var nm = document.createElement("span");
            nm.className = "pf-suggest-name";
            var pos = item.name.indexOf(term);
            if (pos >= 0 && term) {
                nm.appendChild(document.createTextNode(item.name.slice(0, pos)));
                var b = document.createElement("b");
                b.textContent = item.name.slice(pos, pos + term.length);
                nm.appendChild(b);
                nm.appendChild(document.createTextNode(item.name.slice(pos + term.length)));
            } else {
                nm.textContent = item.name;
            }
            row.appendChild(nm);

            if (item.code) {
                var cd = document.createElement("span");
                cd.className = "pf-suggest-code";
                cd.textContent = item.code;
                row.appendChild(cd);
            }

            // 选项B / 选项D 各只取前 15 个字符做提示（与页面 K 批量添加股票的联想一致）
            if (item.optionB) {
                row.classList.add("has-b");
                var ob = document.createElement("span");
                ob.className = "pf-suggest-b";
                ob.textContent = item.optionB.slice(0, 15);
                ob.title = item.optionB;
                row.appendChild(ob);
            }
            if (item.optionD) {
                row.classList.add("has-d");
                var od = document.createElement("span");
                od.className = "pf-suggest-d";
                od.textContent = item.optionD.slice(0, 15);
                od.title = item.optionD;
                row.appendChild(od);
            }

            row.addEventListener("mousedown", function (ev) {
                ev.preventDefault();      // 防止输入框先失焦
                applySuggest(idx);
            });
            row.addEventListener("mouseenter", function () { setSuggestIndex(idx); });
            suggestBox.appendChild(row);
        });
        suggestBox.hidden = false;
    }

    function updateSuggest() {
        var term = (input.value || "").trim();
        if (!term) { hideSuggest(); return; }
        loadAllStocks();
        renderSuggest(term);
    }

    function applySuggest(idx) {
        var item = suggestItems[idx];
        if (!item) return;
        input.value = item.name;
        hideSuggest();
        input.focus();
    }

    if (suggestBox && input) {
        input.addEventListener("input", function () {
            clearTimeout(suggestTimer);
            suggestTimer = setTimeout(updateSuggest, 120);
        });
        input.addEventListener("focus", function () { loadAllStocks(); });
        input.addEventListener("blur", function () { hideSuggest(); });
        input.addEventListener("keydown", function (e) {
            if (e.isComposing || e.keyCode === 229) return;
            if (suggestBox.hidden || !suggestItems.length) return;
            if (e.key === "ArrowDown") {
                e.preventDefault();
                setSuggestIndex((suggestIndex + 1) % suggestItems.length);
            } else if (e.key === "ArrowUp") {
                e.preventDefault();
                setSuggestIndex((suggestIndex - 1 + suggestItems.length) % suggestItems.length);
            } else if (e.key === "Escape") {
                e.preventDefault();
                hideSuggest();
            }
        });
    }

    // 支持 URL 参数 ?q=... 进入时自动搜索（供页面 D 等跳转使用）
    var initQ = new URLSearchParams(window.location.search).get("q");
    if (initQ && initQ.trim()) {
        input.value = initQ.trim();
        doSearch();
    }

    // 无 ?q= 参数时：拉最近一次搜索的关键词并自动搜索一次
    function bootstrapFromLastSearch() {
        if (!input) return;
        if (input.value && input.value.trim()) return; // 已有内容就不覆盖
        fetch("/api/search_history/recent?limit=1", { method: "GET" })
            .then(function (r) { return r.json(); })
            .then(function (data) {
                var items = data.items || [];
                if (!items.length) return;
                var last = items[0];
                if (!last || !last.keyword) return;
                input.value = last.keyword;
                doSearch();
            })
            .catch(function () { /* 静默失败，不影响页面 */ });
    }

    // ====== 搜索历史：渲染、点击回填、清空 ======
    function renderSearchHistory(items) {
        if (!historyRow || !historyChips) return;
        historyChips.innerHTML = "";
        if (!items || !items.length) {
            historyRow.hidden = true;
            return;
        }
        items.forEach(function (it) {
            var chip = document.createElement("button");
            chip.type = "button";
            chip.className = "search-history-chip";
            chip.title = (it.searched_at || "") +
                " · 命中 " + (it.hit_count || 0) + " 只";
            // 关键词文本
            var txt = document.createElement("span");
            txt.className = "search-history-chip-text";
            txt.textContent = it.keyword;
            chip.appendChild(txt);
            // 命中数量徽标（>0 时显示）
            var cnt = it.hit_count || 0;
            if (cnt > 0) {
                var badge = document.createElement("span");
                badge.className = "search-history-chip-count";
                badge.textContent = String(cnt);
                chip.appendChild(badge);
            }
            chip.addEventListener("click", function () {
                input.value = it.keyword;
                doSearch();
            });
            historyChips.appendChild(chip);
        });
        historyRow.hidden = false;
    }

    function loadSearchHistory() {
        fetch("/api/search_history/recent?limit=10", { method: "GET" })
            .then(function (r) { return r.json(); })
            .then(function (data) { renderSearchHistory(data.items || []); })
            .catch(function () {
                if (historyRow) historyRow.hidden = true;
            });
    }

    // 在 render 完成后刷新历史（搜索成功才记录新历史）
    var _origRender = render;
    render = function (data) {
        _origRender(data);
        loadSearchHistory();
    };

    // 初始加载一次历史
    loadSearchHistory();
    // 没有 ?q= 时，回填最近一次搜索并自动搜索
    if (!initQ || !initQ.trim()) {
        bootstrapFromLastSearch();
    }
})();
