/* 页面 A 脚本：搜索过滤（行业名 / 代码） */
(function () {
    "use strict";

    const $search = document.getElementById("searchInput");
    const $rows = document.querySelectorAll("tr.primary-row");
    const $allSecondary = document.querySelectorAll("li.secondary-item");

    function clearHighlights() {
        document.querySelectorAll("mark.hl").forEach((m) => {
            const text = document.createTextNode(m.textContent);
            m.parentNode.replaceChild(text, m);
        });
        document
            .querySelectorAll(".primary-name, .primary-code, .secondary-name, .secondary-code")
            .forEach((el) => el.normalize());
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

    function applySearch() {
        const kw = ($search.value || "").trim().toLowerCase();
        clearHighlights();

        $rows.forEach((row) => {
            const primaryName = (
                row.querySelector(".primary-name")?.textContent || ""
            ).toLowerCase();
            const primaryCode = (
                row.querySelector(".primary-code")?.textContent || ""
            ).toLowerCase();
            const secondaryItems = row.querySelectorAll("li.secondary-item");
            let secondaryMatchCount = 0;
            let primaryMatch = !kw || primaryName.includes(kw) || primaryCode.includes(kw);

            secondaryItems.forEach((s) => {
                const sName = (
                    s.querySelector(".secondary-name")?.textContent || ""
                ).toLowerCase();
                const sCode = (
                    s.querySelector(".secondary-code")?.textContent || ""
                ).toLowerCase();
                const sMatch = !kw || sName.includes(kw) || sCode.includes(kw);
                s.style.display = sMatch ? "" : "none";
                if (sMatch) {
                    secondaryMatchCount++;
                    if (kw) {
                        highlightText(s.querySelector(".secondary-name"), kw);
                        highlightText(s.querySelector(".secondary-code"), kw);
                    }
                }
            });

            // 整行是否显示
            const rowVisible = !kw || primaryMatch || secondaryMatchCount > 0;
            row.style.display = rowVisible ? "" : "none";

            if (kw && primaryMatch) {
                highlightText(row.querySelector(".primary-name"), kw);
                highlightText(row.querySelector(".primary-code"), kw);
            }
        });
    }

    let timer = null;
    $search.addEventListener("input", () => {
        clearTimeout(timer);
        timer = setTimeout(applySearch, 120);
    });
})();
