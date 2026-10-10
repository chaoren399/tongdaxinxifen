/* 页面 B 脚本：搜索过滤（一级 / 二级 / 三级 / 股票） */
(function () {
    "use strict";

    const $search = document.getElementById("searchInput");
    const $rows = document.querySelectorAll("tr.primary-row");

    function clearHighlights() {
        document.querySelectorAll("mark.hl").forEach((m) => {
            const text = document.createTextNode(m.textContent);
            m.parentNode.replaceChild(text, m);
        });
        document
            .querySelectorAll(
                ".primary-name, .primary-code, .secondary-name, .secondary-code, " +
                ".tertiary-name, .tertiary-code, .stock-name, .stock-code"
            )
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

    function setVisible(el, show) {
        el.style.display = show ? "" : "none";
    }

    function rowText(sel, row) {
        const el = row.querySelector(sel);
        return (el?.textContent || "").toLowerCase();
    }

    function applySearch() {
        const kw = ($search.value || "").trim().toLowerCase();
        clearHighlights();

        $rows.forEach((row) => {
            // 一级
            const primaryHit = !kw ||
                rowText(".primary-name", row).includes(kw) ||
                rowText(".primary-code", row).includes(kw);

            // 二级
            const secondaries = row.querySelectorAll("li.secondary-block");
            let anyVisible = primaryHit;
            secondaries.forEach((s) => {
                const sHit = !kw ||
                    ((s.querySelector(".secondary-name")?.textContent || "")).toLowerCase().includes(kw) ||
                    ((s.querySelector(".secondary-code")?.textContent || "")).toLowerCase().includes(kw);

                // 三级
                const tertiaries = s.querySelectorAll("li.tertiary-block");
                let tertiaryVisible = false;
                tertiaries.forEach((t) => {
                    const tHit = !kw ||
                        ((t.querySelector(".tertiary-name")?.textContent || "")).toLowerCase().includes(kw) ||
                        ((t.querySelector(".tertiary-code")?.textContent || "")).toLowerCase().includes(kw);

                    // 股票
                    const stocks = t.querySelectorAll("li.stock-row");
                    let stockVisibleCount = 0;
                    stocks.forEach((st) => {
                        const stHit = !kw ||
                            ((st.querySelector(".stock-name")?.textContent || "")).toLowerCase().includes(kw) ||
                            ((st.querySelector(".stock-code")?.textContent || "")).toLowerCase().includes(kw);
                        setVisible(st, stHit);
                        if (stHit) {
                            stockVisibleCount++;
                            highlightText(st.querySelector(".stock-name"), kw);
                            highlightText(st.querySelector(".stock-code"), kw);
                        }
                    });

                    const tVisible = tHit || stockVisibleCount > 0;
                    setVisible(t, tVisible);
                    if (tVisible) {
                        tertiaryVisible = true;
                        if (tHit) {
                            highlightText(t.querySelector(".tertiary-name"), kw);
                            highlightText(t.querySelector(".tertiary-code"), kw);
                        }
                    }
                });

                const sVisible = sHit || tertiaryVisible;
                setVisible(s, sVisible);
                if (sVisible) {
                    anyVisible = true;
                    if (sHit) {
                        highlightText(s.querySelector(".secondary-name"), kw);
                        highlightText(s.querySelector(".secondary-code"), kw);
                    }
                }
            });

            setVisible(row, anyVisible);
            if (primaryHit && kw) {
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
