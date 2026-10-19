/* 页面 E：三级行业股票管理（新增 / 编辑 / 删除） */
(function () {
    "use strict";

    const options = window.TERTIARY_OPTIONS || [];

    // ====== 三级行业分页下拉（与页面 C 同款逻辑） ======
    const dropdown = document.getElementById("eTertiaryDropdown");
    const display = document.getElementById("eTertiaryDisplay");
    const panel = document.getElementById("eTertiaryPanel");
    const search = document.getElementById("eTertiarySearch");
    const listEl = document.getElementById("eTertiaryList");
    const pagerEl = document.getElementById("eTertiaryPager");
    const hidden = document.getElementById("eTertiaryPath");

    const PAGE_COUNT = 5;
    const pageCount = Math.min(PAGE_COUNT, options.length);
    const pageSize = Math.ceil(options.length / pageCount) || 1;
    let currentPage = 0;
    let selectedValue = "";
    let selectedLabel = "";

    function closePanel() {
        panel.hidden = true;
        display.classList.remove("open");
    }
    function openPanel() {
        panel.hidden = false;
        display.classList.add("open");
        render();
        if (search) search.focus();
    }

    function renderList() {
        const kw = ((search && search.value) || "").trim().toLowerCase();
        listEl.innerHTML = "";

        let items, hint = "";
        if (kw) {
            items = options.filter((o) => o.label.toLowerCase().includes(kw));
            hint = "（搜索结果）";
        } else {
            const start = currentPage * pageSize;
            items = options.slice(start, start + pageSize);
            hint = `（第 ${currentPage + 1}/${pageCount} 页）`;
        }

        if (items.length === 0) {
            const li = document.createElement("li");
            li.className = "empty-item";
            li.textContent = "— 无匹配结果 —";
            listEl.appendChild(li);
            return;
        }
        items.forEach((o) => {
            const li = document.createElement("li");
            if (o.value === selectedValue) li.className = "active";
            li.textContent = o.label + hint;
            li.addEventListener("click", () => selectOption(o));
            listEl.appendChild(li);
        });
    }

    function renderPager() {
        pagerEl.innerHTML = "";
        if ((search && search.value) || "") return;
        for (let i = 0; i < pageCount; i++) {
            const btn = document.createElement("button");
            btn.type = "button";
            btn.textContent = "第 " + (i + 1) + " 页";
            if (i === currentPage) btn.className = "active";
            btn.addEventListener("click", () => {
                currentPage = i;
                render();
            });
            pagerEl.appendChild(btn);
        }
    }

    function render() {
        renderList();
        renderPager();
    }

    function selectOption(o) {
        selectedValue = o.value;
        selectedLabel = o.label;
        hidden.value = o.value;
        const ph = dropdown.querySelector(".placeholder");
        const st = dropdown.querySelector(".selected-text");
        ph.hidden = true;
        st.hidden = false;
        st.textContent = o.label;
        closePanel();
        document.getElementById("btnAddStock").disabled = false;
        loadStocks(o.value);
    }

    if (dropdown && options.length > 0) {
        display.addEventListener("click", () => {
            if (panel.hidden) openPanel(); else closePanel();
        });
        display.addEventListener("keydown", (e) => {
            if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                if (panel.hidden) openPanel(); else closePanel();
            }
        });
        document.addEventListener("click", (e) => {
            if (!dropdown.contains(e.target)) closePanel();
        });
        if (search) search.addEventListener("input", render);
    }

    // ====== 股票列表加载 ======
    const $body = document.getElementById("stockTableBody");
    const $count = document.getElementById("stockCount");

    function conceptsOf(st) {
        return ["concept1", "concept2", "concept3", "concept4", "concept5"]
            .map((k) => (st[k] || "").toString().trim())
            .filter(Boolean)
            .join(" / ");
    }

    async function loadStocks(path) {
        $body.innerHTML = '<tr><td colspan="8"><div class="e-empty">加载中...</div></td></tr>';
        try {
            const r = await fetch("/api/pageE/stocks?path=" + encodeURIComponent(path));
            const data = await r.json();
            if (!data.ok) {
                $body.innerHTML = '<tr><td colspan="8"><div class="e-empty">' +
                    (data.error || "加载失败") + "</div></td></tr>";
                return;
            }
            $count.textContent = data.stockCount;
            renderStocks(data.stocks || []);
        } catch (err) {
            $body.innerHTML = '<tr><td colspan="8"><div class="e-empty">加载失败：' +
                err.message + "</div></td></tr>";
        }
    }

    function renderStocks(stocks) {
        $body.innerHTML = "";
        if (stocks.length === 0) {
            $body.innerHTML = '<tr><td colspan="6"><div class="e-empty">该三级行业下暂无股票</div></td></tr>';
            return;
        }
        stocks.forEach((st, i) => {
            const tr = document.createElement("tr");

            const tdIdx = document.createElement("td");
            tdIdx.textContent = i + 1;

            const tdName = document.createElement("td");
            tdName.textContent = st.name;

            const tdCode = document.createElement("td");
            tdCode.textContent = st.code || "-";

            const tdConcept = document.createElement("td");
            tdConcept.className = "td-wrap";
            tdConcept.textContent = conceptsOf(st) || "-";

            const tdOption = document.createElement("td");
            tdOption.className = "td-wrap";
            tdOption.textContent = optionsOf(st) || "-";

            const tdAct = document.createElement("td");
            tdAct.className = "actions";

            const btnEdit = document.createElement("button");
            btnEdit.type = "button";
            btnEdit.className = "btn btn-sm";
            btnEdit.textContent = "编辑";
            btnEdit.addEventListener("click", () => openEditModal(st));

            const btnDel = document.createElement("button");
            btnDel.type = "button";
            btnDel.className = "btn btn-sm btn-danger";
            btnDel.textContent = "删除";
            btnDel.addEventListener("click", () => deleteStock(st));

            tdAct.appendChild(btnEdit);
            tdAct.appendChild(btnDel);

            tr.appendChild(tdIdx);
            tr.appendChild(tdName);
            tr.appendChild(tdCode);
            tr.appendChild(tdConcept);
            tr.appendChild(tdOption);
            tr.appendChild(tdAct);
            $body.appendChild(tr);
        });
    }

    // ====== 新增 / 编辑弹窗 ======
    const modal = document.getElementById("stockModal");
    const modalTitle = document.getElementById("modalTitle");
    const modalMsg = document.getElementById("modalMsg");
    const fields = {
        name: document.getElementById("fName"),
        code: document.getElementById("fCode"),
        concept1: document.getElementById("fConcept1"),
        concept2: document.getElementById("fConcept2"),
        concept3: document.getElementById("fConcept3"),
        concept4: document.getElementById("fConcept4"),
        concept5: document.getElementById("fConcept5"),
        option_a: document.getElementById("fOptionA"),
        option_b: document.getElementById("fOptionB"),
        option_c: document.getElementById("fOptionC"),
        option_d: document.getElementById("fOptionD"),
        option_e: document.getElementById("fOptionE"),
        option_f: document.getElementById("fOptionF"),
        option_g: document.getElementById("fOptionG"),
        option_h: document.getElementById("fOptionH"),
    };

    const OPTION_KEYS = [
        "option_a", "option_b", "option_c", "option_d",
        "option_e", "option_f", "option_g", "option_h",
    ];
    const OPTION_LABELS = ["A", "B", "C", "D", "E", "F", "G", "H"];

    function optionsOf(st) {
        return OPTION_KEYS
            .map((k, i) => {
                const v = (st[k] || "").toString().trim();
                return v ? OPTION_LABELS[i] + ":" + v : "";
            })
            .filter(Boolean)
            .join(" / ");
    }

    let mode = "add";      // add | edit
    let editOriginName = "";

    function openModal(title, data) {
        mode = title.indexOf("编辑") === 0 ? "edit" : "add";
        modalTitle.textContent = title;
        modalMsg.textContent = "";
        const d = data || {};
        // 先设置非概念字段
        Object.keys(fields).forEach((k) => {
            if (!/^concept[1-5]$/.test(k)) {
                fields[k].value = (d[k] || "").toString();
            }
        });
        // 拉取一级概念，拆分已有概念后按顺序填入 concept1~5，再显示弹窗
        fetch("/api/concepts/primary")
            .then((r) => r.json())
            .then((cd) => {
                const list = (cd && cd.ok) ? (cd.items || []) : [];
                populateConceptSelects(list);
                const primaryNames = {};
                list.forEach((c) => { primaryNames[c.name] = true; });
                const pieces = [];
                ["concept1", "concept2", "concept3", "concept4", "concept5"].forEach((k) => {
                    const raw = d[k] || "";
                    if (!raw) return;
                    raw.split(/[-，,、\s;；]+/).forEach((p) => {
                        // 完整路径 "固态电池/材料/..." → 只把一级送进下拉
                        const root = p.split("/")[0].trim();
                        if (root && primaryNames[root] && pieces.indexOf(root) === -1) {
                            pieces.push(root);
                        }
                    });
                });
                fields.concept1.value = pieces[0] || "";
                fields.concept2.value = pieces[1] || "";
                fields.concept3.value = pieces[2] || "";
                fields.concept4.value = pieces[3] || "";
                fields.concept5.value = pieces[4] || "";
                modal.hidden = false;
                fields.name.focus();
            })
            .catch(() => {
                populateConceptSelects([]);
                modal.hidden = false;
                fields.name.focus();
            });
    }

    function populateConceptSelects(list) {
        ["fConcept1", "fConcept2", "fConcept3", "fConcept4", "fConcept5"].forEach((id) => {
            const sel = document.getElementById(id);
            if (!sel) return;
            const current = sel.value;
            sel.innerHTML = '<option value="">（空）</option>';
            list.forEach((c) => {
                const name = c.name || "";
                const usage = c.usage ? " (" + c.usage + ")" : "";
                const opt = document.createElement("option");
                opt.value = name;
                opt.textContent = name + usage;
                sel.appendChild(opt);
            });
            sel.value = current;
        });
    }

    function closeModal() {
        modal.hidden = true;
    }

    document.getElementById("btnAddStock").addEventListener("click", () => {
        if (!selectedValue) {
            alert("请先选择三级行业");
            return;
        }
        openModal("新增股票（到 " + selectedLabel.split("（")[0] + "）", {});
    });

    function openEditModal(st) {
        editOriginName = st.name;
        openModal("编辑股票：" + st.name, st);
    }

    document.getElementById("btnCancel").addEventListener("click", closeModal);
    modal.addEventListener("click", (e) => {
        if (e.target === modal) closeModal();
    });
    document.addEventListener("keydown", (e) => {
        if (e.key === "Escape" && !modal.hidden) closeModal();
    });

    // ====== 保存（新增 / 编辑共用） ======
    document.getElementById("btnSave").addEventListener("click", async () => {
        if (!selectedValue) return;
        const name = fields.name.value.trim();
        if (!name) {
            modalMsg.textContent = "股票名称不能为空";
            fields.name.focus();
            return;
        }

        const payloadFields = {
            name: name,
            code: fields.code.value.trim(),
            concept1: fields.concept1.value.trim(),
            concept2: fields.concept2.value.trim(),
            concept3: fields.concept3.value.trim(),
            concept4: fields.concept4.value.trim(),
            concept5: fields.concept5.value.trim(),
        };
        OPTION_KEYS.forEach((k) => {
            payloadFields[k] = fields[k].value.trim();
        });

        try {
            let r;
            if (mode === "add") {
                r = await fetch("/api/pageE/add_stock", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(
                        Object.assign({ path: selectedValue }, payloadFields)
                    ),
                });
            } else {
                r = await fetch("/api/stock/update_concepts", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        path: selectedValue,
                        name: editOriginName,
                        fields: payloadFields,
                    }),
                });
            }
            const data = await r.json();
            if (!data.ok) {
                modalMsg.textContent = data.error || "保存失败";
                return;
            }
            closeModal();
            loadStocks(selectedValue);
        } catch (err) {
            modalMsg.textContent = "保存失败：" + err.message;
        }
    });

    // ====== 删除股票 ======
    async function deleteStock(st) {
        if (!selectedValue) return;
        if (!confirm('确认删除股票「' + st.name + '」？\n该操作会从数据库中永久移除，不可恢复。')) {
            return;
        }
        try {
            const r = await fetch("/api/pageE/delete_stock", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ path: selectedValue, name: st.name }),
            });
            const data = await r.json();
            if (!data.ok) {
                alert(data.error || "删除失败");
                return;
            }
            loadStocks(selectedValue);
        } catch (err) {
            alert("删除失败：" + err.message);
        }
    }
})();
