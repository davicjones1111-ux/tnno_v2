(function(global) {
    'use strict';

    const { setSanitizedHTML } = global.TNNOApp || {};

    function initRecentHistory() {
        const wrapper = document.querySelector('[data-history-page="recent"]');
        if (!wrapper || wrapper.dataset.bound === '1') return;
        wrapper.dataset.bound = '1';

        const endpoint = wrapper.dataset.endpoint;
        const results = document.getElementById('history-results');
        const loading = document.getElementById('history-loading');
        const tabs = Array.from(wrapper.querySelectorAll('.history-tab'));

        function setActive(activeKey) {
            tabs.forEach((tab) => {
                const isActive = tab.dataset.filter === activeKey;
                tab.classList.toggle('active', isActive);
                tab.setAttribute('aria-selected', isActive ? 'true' : 'false');
            });
        }

        function fetchPartial(filterKey) {
            if (!endpoint || !results) return;

            setActive(filterKey);
            if (loading) loading.style.display = 'block';
            results.classList.add('is-loading');

            const url = `${endpoint}?type=${encodeURIComponent(filterKey)}&partial=1`;
            fetch(url, {
                headers: { 'X-Requested-With': 'XMLHttpRequest' },
                credentials: 'same-origin'
            })
                .then((resp) => resp.text())
                .then((html) => {
                    if (setSanitizedHTML) {
                        setSanitizedHTML(results, html);
                    } else {
                        results.textContent = html;
                    }
                    const nextUrl = new URL(global.location.href);
                    nextUrl.searchParams.set('type', filterKey);
                    nextUrl.searchParams.delete('page');
                    global.history.replaceState({}, '', nextUrl.toString());
                })
                .catch(() => {
                    global.location.href = `${endpoint}?type=${encodeURIComponent(filterKey)}`;
                })
                .finally(() => {
                    if (loading) loading.style.display = 'none';
                    results.classList.remove('is-loading');
                });
        }

        tabs.forEach((tab) => {
            tab.addEventListener('click', (event) => {
                event.preventDefault();
                fetchPartial(tab.dataset.filter || 'all');
            });
        });
    }

    function initAdminHistory() {
        const wrapper = document.querySelector('[data-admin-history-page="active"]');
        if (!wrapper || wrapper.dataset.bound === '1') return;
        wrapper.dataset.bound = '1';

        const endpoint = wrapper.dataset.endpoint;
        const results = document.getElementById('admin-history-results');
        const loading = document.getElementById('admin-history-loading');
        const typeTabs = Array.from(wrapper.querySelectorAll('.admin-type-tab'));
        const statusTabs = Array.from(wrapper.querySelectorAll('.admin-status-tab'));
        let currentType = wrapper.dataset.currentType || 'all';
        let currentStatus = wrapper.dataset.currentStatus || 'pending';

        function setActive(tabs, key, attrName) {
            tabs.forEach((tab) => {
                const isActive = tab.dataset[attrName] === key;
                tab.classList.toggle('active', isActive);
                tab.setAttribute('aria-selected', isActive ? 'true' : 'false');
            });
        }

        function fetchPartial(nextType, nextStatus) {
            if (!endpoint || !results) return;

            currentType = nextType;
            currentStatus = nextStatus;
            setActive(typeTabs, currentType, 'type');
            setActive(statusTabs, currentStatus, 'status');

            if (loading) loading.style.display = 'block';
            results.classList.add('is-loading');

            const url = `${endpoint}?type=${encodeURIComponent(currentType)}&status=${encodeURIComponent(currentStatus)}&partial=1`;
            fetch(url, {
                headers: { 'X-Requested-With': 'XMLHttpRequest' },
                credentials: 'same-origin'
            })
                .then((resp) => resp.text())
                .then((html) => {
                    if (setSanitizedHTML) {
                        setSanitizedHTML(results, html);
                    } else {
                        results.textContent = html;
                    }
                    const nextUrl = new URL(global.location.href);
                    nextUrl.searchParams.set('type', currentType);
                    nextUrl.searchParams.set('status', currentStatus);
                    nextUrl.searchParams.delete('page');
                    global.history.replaceState({}, '', nextUrl.toString());
                })
                .catch(() => {
                    global.location.href = `${endpoint}?type=${encodeURIComponent(currentType)}&status=${encodeURIComponent(currentStatus)}`;
                })
                .finally(() => {
                    if (loading) loading.style.display = 'none';
                    results.classList.remove('is-loading');
                });
        }

        typeTabs.forEach((tab) => {
            tab.addEventListener('click', (event) => {
                event.preventDefault();
                fetchPartial(tab.dataset.type || 'all', currentStatus);
            });
        });

        statusTabs.forEach((tab) => {
            tab.addEventListener('click', (event) => {
                event.preventDefault();
                fetchPartial(currentType, tab.dataset.status || 'pending');
            });
        });
    }

    function initHistoryTabs() {
        initRecentHistory();
        initAdminHistory();
    }

    document.addEventListener('DOMContentLoaded', initHistoryTabs);
    document.addEventListener('turbo:load', initHistoryTabs);
})(window);
