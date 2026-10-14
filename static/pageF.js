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

        // 第二列：概念、选项A~H
        var col2 = document.createElement("div");
        col2.className = "stock-detail-col";
        var t2 = document.createElement("table");
        t2.className = "stock-detail-table";
        var tb2 = document.createElement("tbody");
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
        // 概念：有数据才展示，无数据不展示
        if (conceptTexts.length) {
            conceptGroupRow(tb2, "概念", conceptTexts, keywords);
        }

        // 选项A ~ 选项H：每个字段都展示一行，空值显示 —
        var OPTION_FIELDS = [
            { key: "option_a", label: "选项A" },
            { key: "option_b", label: "选项B" },
            { key: "option_c", label: "选项C" },
            { key: "option_d", label: "选项D" },
            { key: "option_e", label: "选项E" },
            { key: "option_f", label: "选项F" },
            { key: "option_g", label: "选项G" },
            { key: "option_h", label: "选项H" },
        ];
        OPTION_FIELDS.forEach(function (f) {
            var v = (it[f.key] || "").trim();
            detailRow(tb2, f.label, v || "—", keywords);
        });
        t2.appendChild(tb2);
        col2.appendChild(t2);
        cols.appendChild(col2);

        // 第三列：图片（只读预览，支持多张；编辑入口在「编辑」弹窗里）
        var col3 = document.createElement("div");
        col3.className = "stock-detail-col stock-detail-image-col";
        var urls = cardImageUrls(it);
        if (urls.length) {
            var grid = document.createElement("div");
            grid.className = "stock-image-grid";
            urls.forEach(function (u, i) {
                var cell = document.createElement("div");
                cell.className = "stock-image-cell";
                var img = document.createElement("img");
                img.src = u;
                img.alt = it.name;
                img.className = "stock-image";
                img.loading = "lazy";
                img.addEventListener("click", function () {
                    openLightbox(urls, i, it.name);
                });
                cell.appendChild(img);
                grid.appendChild(cell);
            });
            col3.appendChild(grid);
        } else {
            var imgWrap = document.createElement("div");
            imgWrap.className = "stock-image-wrap";
            var ph = document.createElement("div");
            ph.className = "stock-image-placeholder";
            ph.textContent = "暂无图片\n点击右上「编辑」上传";
            imgWrap.appendChild(ph);
            col3.appendChild(imgWrap);
        }
        cols.appendChild(col3);

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
                    var t = p.trim();
                    if (t && primaryNames[t] && conceptPieces.indexOf(t) === -1) {
                        conceptPieces.push(t);
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
                '<div class="batch-modal-section-title">概念（从一级概念中选择）</div>' +
                buildConceptSelects(conceptFields) +
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
                '</div>';

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
