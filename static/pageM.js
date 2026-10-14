(function () {
    "use strict";

    var list = document.getElementById("pageMList");
    var totalEl = document.getElementById("pageMTotal");
    var daysEl = document.getElementById("pageMDays");
    var hitsEl = document.getElementById("pageMHits");
    var daysSel = document.getElementById("pageMDaysSelect");
    var filterInput = document.getElementById("pageMFilterInput");
    var refreshBtn = document.getElementById("pageMRefreshBtn");

    var state = {
        items: [],
        groups: [],
        days: 30,
        filter: "",
        loading: false,
    };

    function escapeHtml(s) {
        return String(s == null ? "" : s)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#39;");
    }

    function escapeRegex(s) {
        return String(s).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    }

    function highlight(text, kw) {
        var safe = escapeHtml(text);
        if (!kw) return safe;
        return safe.replace(
            new RegExp(escapeRegex(escapeHtml(kw)), "gi"),
            function (m) { return "<mark>" + m + "</mark>"; }
        );
    }

    function fmtTime(s) {
        // "YYYY-MM-DD HH:MM:SS" -> "HH:MM:SS"
        var idx = String(s).indexOf(" ");
        return idx >= 0 ? String(s).substring(idx + 1, idx + 9) : String(s);
    }

    function fmtDateLabel(dateStr) {
        var today = new Date();
        var ymd = today.toISOString().slice(0, 10);
        var ymd1 = new Date(today.getTime() - 86400000).toISOString().slice(0, 10);
        if (dateStr === ymd) return dateStr + "（今天）";
        if (dateStr === ymd1) return dateStr + "（昨天）";
        return dateStr;
    }

    function isToday(dateStr) {
        var today = new Date().toISOString().slice(0, 10);
        return dateStr === today;
    }

    function setStats() {
        var total = state.items.length;
        var totalHits = 0;
        state.items.forEach(function (it) { totalHits += Number(it.hit_count || 0); });
        totalEl.textContent = total;
        hitsEl.textContent = totalHits;
        daysEl.textContent = state.groups.length;
    }

    function render() {
        var filter = state.filter.trim().toLowerCase();
        // 过滤
        var visible = state.items.filter(function (it) {
            if (!filter) return true;
            return String(it.keyword || "").toLowerCase().indexOf(filter) >= 0;
        });
        // 重新按日期分组
        var gmap = {};
        visible.forEach(function (it) {
            var d = String(it.searched_at || "").slice(0, 10);
            var g = gmap[d] || (gmap[d] = { date: d, rows: [], hits: 0 });
            g.rows.push(it);
            g.hits += Number(it.hit_count || 0);
        });
        var groups = Object.keys(gmap).sort(function (a, b) { return a < b ? 1 : -1; })
            .map(function (k) { return gmap[k]; });

        if (!groups.length) {
            list.innerHTML = '<div class="pageM-empty">没有匹配的搜索记录</div>';
            return;
        }

        var html = "";
        groups.forEach(function (g) {
            html += '<section class="pageM-group">';
            html += '<header class="pageM-group-head">';
            var dateCls = "pageM-group-date" + (isToday(g.date) ? " is-today" : "");
            html += '<span class="' + dateCls + '">' + escapeHtml(fmtDateLabel(g.date)) + '</span>';
            html += '<span class="pageM-group-stats">';
            html += '<span>搜索 <b>' + g.rows.length + '</b> 次</span>';
            html += '<span>命中 <b>' + g.hits + '</b> 只</span>';
            html += '</span>';
            html += '</header>';
            html += '<div class="pageM-rows">';
            g.rows.forEach(function (it) {
                var kwHtml = highlight(it.keyword || "", filter);
                var hitsHtml = '<span class="pageM-row-hits' + ((it.hit_count || 0) === 0 ? " zero" : "") + '">命中 ' + (it.hit_count || 0) + '</span>';
                var btnHtml = '<a class="pageM-row-btn" href="/pageF?q=' + encodeURIComponent(it.keyword || "") + '">回填搜索</a>';
                html += '<div class="pageM-row">';
                html += '<span class="pageM-row-time">' + escapeHtml(fmtTime(it.searched_at)) + '</span>';
                html += '<span class="pageM-row-keyword" title="' + escapeHtml(it.keyword || "") + '">' + kwHtml + '</span>';
                html += '<span class="pageM-row-id">#' + (it.id || "") + '</span>';
                html += hitsHtml;
                html += '<span class="pageM-row-actions">' + btnHtml + '</span>';
                html += '</div>';
            });
            html += '</div>';
            html += '</section>';
        });
        list.innerHTML = html;
    }

    function load() {
        if (state.loading) return;
        state.loading = true;
        refreshBtn.disabled = true;
        list.innerHTML = '<div class="pageM-loading">加载中…</div>';
        fetch("/api/search_history/list?days=" + encodeURIComponent(state.days) + "&limit=2000", {
            method: "GET"
        })
            .then(function (r) { return r.json(); })
            .then(function (data) {
                state.items = data.items || [];
                state.groups = data.groups || [];
                setStats();
                render();
            })
            .catch(function () {
                list.innerHTML = '<div class="pageM-empty">加载失败，请重试</div>';
            })
            .then(function () {
                state.loading = false;
                refreshBtn.disabled = false;
            });
    }

    daysSel.addEventListener("change", function () {
        state.days = parseInt(daysSel.value, 10) || 30;
        load();
    });

    filterInput.addEventListener("input", function () {
        state.filter = filterInput.value || "";
        render();
    });

    refreshBtn.addEventListener("click", load);

    // 初始加载
    load();
})();
