(function(global) {
    'use strict';

    const app = global.TNNOApp || {};

    function debounce(func, wait) {
        let timeout;
        return function(...args) {
            global.clearTimeout(timeout);
            timeout = global.setTimeout(() => func.apply(this, args), wait);
        };
    }

    function buildMetaIcon(pathD, extraLine) {
        const svg = app.createElement('svg', {
            attrs: {
                width: '14',
                height: '14',
                viewBox: '0 0 24 24',
                fill: 'none',
                stroke: 'currentColor',
                'stroke-width': '2'
            }
        });
        appendSvgShape(svg, 'path', { d: pathD });
        if (extraLine) {
            extraLine.forEach((shape) => appendSvgShape(svg, shape.tag, shape.attrs));
        }
        return svg;
    }

    function appendSvgShape(svg, tagName, attrs) {
        const node = document.createElementNS('http://www.w3.org/2000/svg', tagName);
        Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, value));
        svg.appendChild(node);
    }

    function buildMissionCard(mission) {
        const card = app.createElement('div', {
            className: `mission-card ${mission.image_path ? 'has-image' : ''}`,
            dataset: { missionId: mission.id }
        });

        if (mission.image_path) {
            const imageWrap = app.createElement('div', { className: 'mission-card-image' });
            const img = app.createElement('img', {
                attrs: {
                    src: app.mediaUrl(mission.image_path),
                    alt: app.toText(mission.title)
                }
            });
            imageWrap.appendChild(img);
            card.appendChild(imageWrap);
        }

        const body = app.createElement('div', { className: 'mission-card-body' });
        const header = app.createElement('div', { className: 'mission-card-header' });
        const rewardBadge = app.createElement('div', { className: 'mission-reward-badge' });
        rewardBadge.appendChild(app.createElement('span', { className: 'px-coin' }));
        rewardBadge.appendChild(document.createTextNode(` ${mission.reward}`));
        header.appendChild(rewardBadge);

        const typeBadgeClass = mission.mission_type && mission.mission_type !== 'default'
            ? `mission-type-badge type-${mission.mission_type}`
            : 'mission-type-badge';
        header.appendChild(app.createElement('div', {
            className: typeBadgeClass,
            text: mission.mission_type && mission.mission_type !== 'default' ? mission.mission_type : 'Default'
        }));
        body.appendChild(header);

        body.appendChild(app.createElement('h3', {
            className: 'mission-title',
            text: mission.title
        }));

        body.appendChild(app.createElement('p', {
            className: 'mission-instructions',
            text: mission.instructions || ''
        }));

        const meta = app.createElement('div', { className: 'mission-meta' });
        const timeItem = app.createElement('span', { className: 'meta-item' });
        const timeSvg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        timeSvg.setAttribute('width', '14');
        timeSvg.setAttribute('height', '14');
        timeSvg.setAttribute('viewBox', '0 0 24 24');
        timeSvg.setAttribute('fill', 'none');
        timeSvg.setAttribute('stroke', 'currentColor');
        timeSvg.setAttribute('stroke-width', '2');
        appendSvgShape(timeSvg, 'circle', { cx: '12', cy: '12', r: '10' });
        appendSvgShape(timeSvg, 'polyline', { points: '12 6 12 12 16 14' });
        timeItem.appendChild(timeSvg);
        timeItem.appendChild(document.createTextNode(` ${mission.time_limit}h`));
        meta.appendChild(timeItem);

        const countItem = app.createElement('span', { className: 'meta-item' });
        const countSvg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        countSvg.setAttribute('width', '14');
        countSvg.setAttribute('height', '14');
        countSvg.setAttribute('viewBox', '0 0 24 24');
        countSvg.setAttribute('fill', 'none');
        countSvg.setAttribute('stroke', 'currentColor');
        countSvg.setAttribute('stroke-width', '2');
        appendSvgShape(countSvg, 'path', { d: 'M16 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2' });
        appendSvgShape(countSvg, 'circle', { cx: '8.5', cy: '7', r: '4' });
        appendSvgShape(countSvg, 'line', { x1: '20', y1: '8', x2: '20', y2: '14' });
        appendSvgShape(countSvg, 'line', { x1: '23', y1: '11', x2: '17', y2: '11' });
        countItem.appendChild(countSvg);
        const availability = mission.limit_count > 0 ? `${mission.limit_count} available` : 'Always available';
        countItem.appendChild(document.createTextNode(` ${availability}`));
        meta.appendChild(countItem);
        body.appendChild(meta);

        const actions = app.createElement('div', { className: 'mission-actions' });
        if (mission.submission_status === 'pending') {
            actions.appendChild(app.createElement('span', {
                className: 'px-status px-status-pending',
                text: 'Pending'
            }));
        } else if (mission.submission_status === 'completed') {
            actions.appendChild(app.createElement('span', {
                className: 'px-status px-status-completed',
                text: 'Completed'
            }));
        } else if (mission.submission_status === 'rejected') {
            actions.appendChild(app.createElement('span', {
                className: 'px-status px-status-rejected',
                text: 'Rejected'
            }));
        }

        const actionLabel = mission.submission_status === 'pending'
            ? 'View'
            : mission.submission_status === 'rejected'
                ? 'Submit Again'
                : mission.submission_status === 'completed'
                    ? 'Start Again'
                    : 'Start Mission';
        const actionClass = mission.submission_status === 'pending'
            ? 'px-btn px-btn-secondary px-btn-sm'
            : 'px-btn px-btn-primary px-btn-sm';
        actions.appendChild(app.createElement('a', {
            className: actionClass,
            text: actionLabel,
            attrs: {
                href: `/missions/${mission.id}`,
                'data-turbo-preload': 'true'
            }
        }));

        body.appendChild(actions);
        card.appendChild(body);
        return card;
    }

    function initMissionsPage() {
        const page = document.querySelector('.missions-page');
        if (!page || page.dataset.bound === '1') return;
        page.dataset.bound = '1';

        let currentPage = Number(page.dataset.initialPage || 1);
        let isLoading = false;
        let hasMore = (page.dataset.initialHasNext || 'false') === 'true';
        let searchQuery = (page.dataset.initialSearch || '').trim();
        const limit = Number(page.dataset.limit || 10);
        const apiUrl = page.dataset.apiUrl;

        const feed = document.getElementById('missionsFeed');
        const loadingIndicator = document.getElementById('loadingIndicator');
        const endMessage = document.getElementById('endMessage');
        const loadMoreContainer = document.getElementById('loadMoreContainer');
        const searchInput = document.getElementById('missionSearch');
        const clearSearchBtn = document.getElementById('clearSearch');
        const totalCount = document.getElementById('totalCount');
        const loadMoreBtn = document.getElementById('loadMoreBtn');

        async function loadMissions() {
            if (isLoading || !hasMore) return;
            isLoading = true;
            if (loadingIndicator) loadingIndicator.style.display = 'flex';

            try {
                const params = new URLSearchParams({
                    page: currentPage + 1,
                    limit
                });
                if (searchQuery) params.append('search', searchQuery);

                const response = await fetch(`${apiUrl}?${params.toString()}`, {
                    credentials: 'same-origin',
                    headers: { 'Accept': 'application/json' }
                });
                if (!response.ok) throw new Error('Failed to load missions');

                const data = await response.json();
                if (data.missions && data.missions.length > 0) {
                    renderMissions(data.missions);
                    currentPage = data.page;
                    hasMore = Boolean(data.has_next);
                    if (!hasMore && endMessage) endMessage.style.display = 'block';
                } else {
                    hasMore = false;
                    if (endMessage) endMessage.style.display = 'block';
                }
            } catch (error) {
                console.error('Error loading missions:', error);
                if (loadMoreContainer) loadMoreContainer.style.display = 'block';
            } finally {
                isLoading = false;
                if (loadingIndicator) loadingIndicator.style.display = 'none';
            }
        }

        function renderMissions(missions) {
            missions.forEach((mission) => {
                feed.appendChild(buildMissionCard(mission));
            });
        }

        function renderEmptySearchState(query) {
            const empty = app.createElement('div', { className: 'empty-state' });
            empty.appendChild(app.createElement('div', { className: 'empty-icon', text: '🔍' }));
            empty.appendChild(app.createElement('h3', { text: 'No Results' }));
            empty.appendChild(app.createElement('p', {
                className: 'px-text-muted',
                text: `No missions match "${query}"`
            }));
            feed.appendChild(empty);
        }

        const handleSearch = debounce(async function(query) {
            searchQuery = query;
            currentPage = 1;
            hasMore = true;
            feed.replaceChildren();

            if (endMessage) endMessage.style.display = 'none';
            if (loadMoreContainer) loadMoreContainer.style.display = 'none';

            if (!query) {
                if (loadingIndicator) loadingIndicator.style.display = 'none';
                if (clearSearchBtn) clearSearchBtn.style.display = 'none';
                global.location.href = '/missions';
                return;
            }

            if (clearSearchBtn) clearSearchBtn.style.display = 'flex';
            if (loadingIndicator) loadingIndicator.style.display = 'flex';

            try {
                const params = new URLSearchParams({ page: 1, limit, search: query });
                const response = await fetch(`${apiUrl}?${params.toString()}`, {
                    credentials: 'same-origin',
                    headers: { 'Accept': 'application/json' }
                });
                if (!response.ok) throw new Error('Search failed');

                const data = await response.json();
                if (data.missions && data.missions.length > 0) {
                    renderMissions(data.missions);
                    currentPage = data.page;
                    hasMore = Boolean(data.has_next);
                    if (totalCount) totalCount.textContent = data.total;
                    if (!hasMore && endMessage) endMessage.style.display = 'block';
                } else {
                    renderEmptySearchState(query);
                    if (endMessage) endMessage.style.display = 'none';
                }
            } catch (error) {
                console.error('Search error:', error);
            } finally {
                if (loadingIndicator) loadingIndicator.style.display = 'none';
            }
        }, 400);

        searchInput.addEventListener('input', function() {
            handleSearch(this.value.trim());
        });

        clearSearchBtn.addEventListener('click', () => {
            searchInput.value = '';
            handleSearch('');
        });

        const observer = new IntersectionObserver((entries) => {
            if (entries[0].isIntersecting && hasMore && !isLoading && !searchQuery) {
                loadMissions();
            }
        }, { rootMargin: '100px' });

        if (endMessage) observer.observe(endMessage);
        if (loadMoreBtn) {
            loadMoreBtn.addEventListener('click', function() {
                this.disabled = true;
                this.textContent = 'Loading...';
                loadMissions().finally(() => {
                    this.disabled = false;
                    this.textContent = 'Load More Missions';
                });
            });
        }

        if (!hasMore && endMessage) {
            endMessage.style.display = 'block';
        }
    }

    document.addEventListener('DOMContentLoaded', initMissionsPage);
    document.addEventListener('turbo:load', initMissionsPage);
})(window);
