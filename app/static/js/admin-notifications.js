(function() {
    'use strict';

    function initNotifUserCheck() {
        const form = document.querySelector('[data-admin-notifications-search-url]');
        const input = document.querySelector('input[name="user_query"]');
        const status = document.getElementById('notif-user-status');
        if (!form || !input || !status || form.dataset.bound === '1') return;
        form.dataset.bound = '1';

        const searchUrl = form.dataset.adminNotificationsSearchUrl;
        let timer = null;

        async function checkUser() {
            const q = input.value.trim();
            if (!q) {
                status.style.display = 'none';
                status.textContent = '';
                return;
            }

            try {
                const res = await fetch(`${searchUrl}?q=${encodeURIComponent(q)}`, {
                    credentials: 'same-origin',
                    headers: { 'Accept': 'application/json' }
                });
                const data = await res.json();
                status.style.display = 'block';
                if (data.results && data.results.length > 0) {
                    status.style.color = 'green';
                    status.textContent = 'User is on';
                } else {
                    status.style.color = 'red';
                    status.textContent = 'User not found';
                }
            } catch (_error) {
                status.style.display = 'block';
                status.style.color = 'red';
                status.textContent = 'User not found';
            }
        }

        input.addEventListener('input', function() {
            globalThis.clearTimeout(timer);
            timer = globalThis.setTimeout(checkUser, 300);
        });
    }

    document.addEventListener('DOMContentLoaded', initNotifUserCheck);
    document.addEventListener('turbo:load', initNotifUserCheck);
})();
