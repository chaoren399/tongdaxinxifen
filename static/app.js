/* 首页 30 个一级行业卡片 + 弹窗逐级加载下级行业 */
(function () {
    "use strict";

    const $primaryList = document.getElementById("primaryList");
    const $search = document.getElementById("searchInput");
    const $modal = document.getElementById("modal");
    const $modalTitle = document.getElementById("modalTitle");
    const $modalBread = document.getElementById("modalBread");
    const $modalBody = document.getElementById("modalBody");
    const $modalClose = document.getElementById("modalClose");

    // 弹窗栈：用于返回上一级
    const stack = [];

    // ====== 搜索 ======
    function clearHighlight() {
        $primaryList.querySelectorAll("mark.hl").forEach((m) => {
            const text = document.createTextNode(m.textContent);
            m.parentNode.replaceChild(text, m);
        });
        $primaryList.querySelectorAll(".primary-name, .primary-code").forEach((el) => el.normalize());
    }

    function highlightText(el, keyword) {
        if (!el || !keyword) return;
        const text = el.textContent;
        const lower = text.toLowerCase();
        const kw = keyword.toLowerCase();
        const idx = lower.indexOf(kw);
        if (idx < 0) return;
        el.textContent = "";
        el.appendChild(document.createTextNode(text.slice(0, idx)));
        const mark = document.createElement("mark");
        mark.className = "hl";
        mark.textContent = text.slice(idx, idx + keyword.length);
        el.appendChild(mark);
        el.appendChild(
            document.createTextNode(text.slice(idx + keyword.length))
        );
    }

    let searchTimer = null;
    $search.addEventListener("input", () => {
        clearTimeout(searchTimer);
        searchTimer = setTimeout(applySearch, 120);
    });

    function applySearch() {
        const kw = ($search.value || "").trim().toLowerCase();
        clearHighlight();

        $primaryList.querySelectorAll(".primary-card").forEach((card) => {
            const nameEl = card.querySelector(".primary-name");
            const codeEl = card.querySelector(".primary-code");
            const haystack = (
                (nameEl?.textContent || "") +
                " " +
                (codeEl?.textContent || "")
            ).toLowerCase();
            if (!kw || haystack.includes(kw)) {
                card.classList.remove("hidden");
                highlightText(nameEl, kw);
                highlightText(codeEl, kw);
            } else {
                card.classList.add("hidden");
            }
        });
    }

    // ====== 一级卡片点击 → 打开弹窗并加载二级 ======
    $primaryList.addEventListener("click", (e) => {
        const card = e.target.closest(".primary-card");
        if (!card) return;
        const name = card.dataset.name;
        openSecondaries(name);
    });

    // ====== 弹窗工具 ======
    function openModal() {
        $modal.hidden = false;
        document.body.style.overflow = "hidden";
    }
    function closeModal() {
        $modal.hidden = true;
        document.body.style.overflow = "";
        stack.length = 0;
    }
    $modalClose.addEventListener("click", closeModal);
    $modal.querySelector(".modal-mask").addEventListener("click", closeModal);
    document.addEventListener("keydown", (e) => {
        if (e.key === "Escape" && !$modal.hidden) closeModal();
    });

    function setBreadcrumb(items) {
        $modalBread.innerHTML = "";
        items.forEach((it, idx) => {
            const span = document.createElement("span");
            if (idx === items.length - 1) {
                span.innerHTML = `<b>${escapeHtml(it.label)}</b>`;
            } else {
                span.textContent = it.label;
                span.style.cursor = "pointer";
                span.addEventListener("click", () => it.onClick());
            }
            $modalBread.appendChild(span);
        });
    }

    function setLoading(text) {
        $modalBody.innerHTML = "";
        const div = document.createElement("div");
        div.className = "loading";
        div.textContent = text || "加载中...";
        $modalBody.appendChild(div);
    }

    function setEmpty(text) {
        $modalBody.innerHTML = "";
        const div = document.createElement("div");
        div.className = "empty";
        div.textContent = text || "暂无数据";
        $modalBody.appendChild(div);
    }

    function escapeHtml(s) {
        return String(s ?? "").replace(/[&<>"']/g, (c) => ({
            "&": "&amp;",
            "<": "&lt;",
            ">": "&gt;",
            '"': "&quot;",
            "'": "&#39;",
        }[c]));
    }

    async function fetchJson(url) {
        const r = await fetch(url);
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
    }

    // ====== 打开一级：列出二级 ======
    async function openSecondaries(primaryName) {
        openModal();
        $modalTitle.textContent = primaryName;
        stack.length = 0;
        stack.push({ level: "primary", name: primaryName });
        setBreadcrumb([{ label: primaryName, onClick: () => openSecondaries(primaryName) }]);
        setLoading();
        try {
            const data = await fetchJson(
                `/api/primary/${encodeURIComponent(primaryName)}/secondaries`
            );
            renderSecondaries(primaryName, data);
        } catch (err) {
            setEmpty(`加载失败: ${err.message}`);
        }
    }

    function renderSecondaries(primaryName, list) {
        if (!list || list.length === 0) {
            setEmpty("该一级行业下暂无二级行业");
            return;
        }
        const ul = document.createElement("ul");
        ul.className = "sub-list";
        list.forEach((s) => {
            const li = document.createElement("li");
            li.className = "sub-item";
            li.innerHTML = `
                <div class="head">
                    <span class="tag tag-secondary">二级</span>
                    <span class="name">${escapeHtml(s.name)}</span>
                </div>
                <div class="meta">三级 <b>${s.tertiaryCount}</b> · 股票 <b>${s.stockCount}</b></div>
            `;
            li.addEventListener("click", () => openTertiaries(primaryName, s.name, s));
            ul.appendChild(li);
        });
        $modalBody.innerHTML = "";
        $modalBody.appendChild(ul);
    }

    // ====== 打开二级：列出三级 ======
    async function openTertiaries(primaryName, secondaryName, secondary) {
        $modalTitle.textContent = secondaryName;
        setBreadcrumb([
            { label: primaryName, onClick: () => openSecondaries(primaryName) },
            { label: secondaryName },
        ]);
        setLoading();
        try {
            const data = await fetchJson(
                `/api/primary/${encodeURIComponent(primaryName)}/secondary/${encodeURIComponent(secondaryName)}/tertiaries`
            );
            renderTertiaries(primaryName, secondaryName, secondary, data);
        } catch (err) {
            setEmpty(`加载失败: ${err.message}`);
        }
    }

    function renderTertiaries(primaryName, secondaryName, secondary, list) {
        if (!list || list.length === 0) {
            setEmpty("该二级行业下暂无三级行业");
            return;
        }
        const ul = document.createElement("ul");
        ul.className = "sub-list";
        list.forEach((t) => {
            const li = document.createElement("li");
            li.className = "sub-item";
            li.innerHTML = `
                <div class="head">
                    <span class="tag tag-tertiary">三级</span>
                    <span class="name">${escapeHtml(t.name)}</span>
                </div>
                <div class="meta">股票 <b>${t.stockCount}</b></div>
            `;
            li.addEventListener("click", () =>
                openStocks(primaryName, secondaryName, t.name, t)
            );
            ul.appendChild(li);
        });
        $modalBody.innerHTML = "";
        $modalBody.appendChild(ul);
    }

    // ====== 打开三级：列出股票 ======
    async function openStocks(primaryName, secondaryName, tertiaryName, tertiary) {
        $modalTitle.textContent = tertiaryName;
        setBreadcrumb([
            { label: primaryName, onClick: () => openSecondaries(primaryName) },
            { label: secondaryName, onClick: () => openTertiaries(primaryName, secondaryName) },
            { label: tertiaryName },
        ]);
        setLoading();
        try {
            const data = await fetchJson(
                `/api/primary/${encodeURIComponent(primaryName)}/secondary/${encodeURIComponent(secondaryName)}/tertiary/${encodeURIComponent(tertiaryName)}/stocks`
            );
            renderStocks(primaryName, secondaryName, tertiaryName, tertiary, data);
        } catch (err) {
            setEmpty(`加载失败: ${err.message}`);
        }
    }

    function renderStocks(primaryName, secondaryName, tertiaryName, tertiary, list) {
        if (!list || list.length === 0) {
            setEmpty("该三级行业暂无股票数据");
            return;
        }
        const ul = document.createElement("ul");
        ul.className = "sub-list stocks";
        list.forEach((s) => {
            const li = document.createElement("li");
            li.className = "stock-item";
            li.innerHTML = `
                <span class="stock-name" data-stock="${escapeHtml(s.name)}">${escapeHtml(s.name)}</span>
                <span class="stock-code" hidden>${escapeHtml(s.code || "-")}</span>
            `;
            ul.appendChild(li);
        });
        $modalBody.innerHTML = "";
        $modalBody.appendChild(ul);
    }
})();
