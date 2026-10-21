/* 手机端通用脚本（独立于 PC 端 static/*.js） */
(function () {
    'use strict';

    // 返回按钮：优先回到上一个页面，若没有历史记录才跳首页
    var back = document.querySelector('.m-back');
    if (back) {
        back.addEventListener('click', function (e) {
            if (history.length > 1) {
                e.preventDefault();
                history.back();
            }
        });
    }

    // 列表点击的轻反馈（移动端无 hover，用 active 状态替代已在 CSS 处理）
    document.addEventListener('touchstart', function () {}, { passive: true });
})();
