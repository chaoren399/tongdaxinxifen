/* 页面 C：在三级行业下新增股票 */
(function () {
    "use strict";

    const $form = document.getElementById("addStockForm");
    const $path = document.getElementById("tertiaryPath");
    const $name = document.getElementById("stockName");
    const $code = document.getElementById("stockCode");
    const $loc = document.getElementById("stockLoc");
    const $main = document.getElementById("stockMain");
    const $result = document.getElementById("addResult");
    const $addedCount = document.getElementById("addedCount");
    const $reset = document.getElementById("resetAdded");

    function showResult(ok, msg) {
        $result.hidden = false;
        $result.className = "add-result " + (ok ? "ok" : "err");
        $result.textContent = msg;
        if (ok) {
            setTimeout(() => {
                $result.hidden = true;
            }, 2500);
        }
    }

    $form.addEventListener("submit", async (e) => {
        e.preventDefault();
        const payload = {
            path: $path.value.trim(),
            name: $name.value.trim(),
            code: $code.value.trim(),
            location: $loc.value.trim(),
            main_business: $main.value.trim(),
        };
        if (!payload.path) {
            showResult(false, "请选择三级行业");
            $path.focus();
            return;
        }
        if (!payload.name) {
            showResult(false, "请填写股票名称");
            $name.focus();
            return;
        }

        const submitBtn = $form.querySelector("button[type=submit]");
        submitBtn.disabled = true;
        submitBtn.textContent = "提交中...";
        try {
            const r = await fetch("/api/pageC/add_stock", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload),
            });
            const data = await r.json();
            if (!r.ok || !data.ok) {
                showResult(false, data.error || `提交失败 (HTTP ${r.status})`);
            } else {
                showResult(true, `已新增「${data.record.stock.name}」到「${data.record.tertiary}」`);
                $addedCount.textContent = String(parseInt($addedCount.textContent || "0", 10) + 1);
                // 重置名称、代码、所在地、主营（保留行业选择，方便连续添加）
                $name.value = "";
                $code.value = "";
                $loc.value = "";
                $main.value = "";
                $name.focus();
            }
        } catch (err) {
            showResult(false, "网络错误：" + err.message);
        } finally {
            submitBtn.disabled = false;
            submitBtn.textContent = "新增";
        }
    });

    // ====== 批量新增 ======
    const $batchForm = document.getElementById("batchForm");
    const $batchPath = document.getElementById("batchTertiaryPath");
    const $batchNames = document.getElementById("batchNames");
    const $batchResult = document.getElementById("batchResult");

    function showBatchResult(ok, msg) {
        $batchResult.hidden = false;
        $batchResult.className = "add-result " + (ok ? "ok" : "err");
        $batchResult.textContent = msg;
        if (ok) {
            setTimeout(() => {
                $batchResult.hidden = true;
            }, 3500);
        }
    }

    $batchForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const path = $batchPath.value.trim();
        const names = $batchNames.value.trim();
        const lines = names
            .split("\n")
            .map((s) => s.trim())
            .filter(Boolean);
        if (!path) {
            showBatchResult(false, "请选择三级行业");
            const disp = document.getElementById("batchTertiaryDisplay");
            if (disp) disp.click();
            return;
        }
        if (lines.length === 0) {
            showBatchResult(false, "请填写至少一个股票名称（每行一个）");
            $batchNames.focus();
            return;
        }
        if (lines.length > 500) {
            showBatchResult(false, `共 ${lines.length} 行，超过单次上限 500`);
            return;
        }
        if (!confirm(`确认为「${path.split("||")[2]}」新增 ${lines.length} 只股票？`)) return;

        const submitBtn = $batchForm.querySelector("button[type=submit]");
        submitBtn.disabled = true;
        submitBtn.textContent = "提交中...";
        try {
            const r = await fetch("/api/pageC/add_stocks_batch", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ path, names }),
            });
            const data = await r.json();
            if (!r.ok || !data.ok) {
                showBatchResult(false, data.error || `提交失败 (HTTP ${r.status})`);
            } else {
                showBatchResult(
                    true,
                    `已批量新增 ${data.count} 只股票到「${path.split("||")[2]}」，当前共 ${data.stockCount} 只`
                );
                $addedCount.textContent = String(
                    parseInt($addedCount.textContent || "0", 10) + data.count
                );
                $batchNames.value = "";
                $batchNames.focus();
            }
        } catch (err) {
            showBatchResult(false, "网络错误：" + err.message);
        } finally {
            submitBtn.disabled = false;
            submitBtn.textContent = "批量新增";
        }
    });

    $reset.addEventListener("click", async () => {
        if (!confirm("确认撤销本次新增的所有股票？\n这会同时从行业表格和 CSV 数据文件中删除对应行。")) return;
        try {
            const r = await fetch("/api/pageC/reset", { method: "POST" });
            if (r.ok) {
                location.reload();
            }
        } catch (err) {
            alert("重置失败：" + err.message);
        }
    });

    // ====== 批量新增：三级行业分页式下拉（平铺列表分 5 页展示） ======
    (function initPagedDropdown() {
        const options = window.TERTIARY_OPTIONS || [];
        const dropdown = document.getElementById("batchTertiaryDropdown");
        if (!dropdown || options.length === 0) return;

        const display = document.getElementById("batchTertiaryDisplay");
        const panel = document.getElementById("batchTertiaryPanel");
        const search = document.getElementById("batchTertiarySearch");
        const listEl = document.getElementById("batchTertiaryList");
        const pagerEl = document.getElementById("batchTertiaryPager");
        const hidden = document.getElementById("batchTertiaryPath");

        const PAGE_COUNT = 5; // 固定分 5 页
        const pageCount = Math.min(PAGE_COUNT, options.length);
        const pageSize = Math.ceil(options.length / pageCount);
        let currentPage = 0;
        let selectedValue = "";

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
                hint = kw ? "（搜索结果）" : "";
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
                li.addEventListener("click", () => {
                    selectedValue = o.value;
                    hidden.value = o.value;
                    const ph = dropdown.querySelector(".placeholder");
                    const st = dropdown.querySelector(".selected-text");
                    ph.hidden = true;
                    st.hidden = false;
                    st.textContent = o.label;
                    closePanel();
                });
                listEl.appendChild(li);
            });
        }

        function renderPager() {
            pagerEl.innerHTML = "";
            if ((search && search.value) || "") return; // 搜索时不分页
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
        if (search) {
            search.addEventListener("input", render);
        }
    })();
})();
