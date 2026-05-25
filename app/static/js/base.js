(function(global) {
    'use strict';

    if (typeof global.Turbo !== 'undefined') {
        global.Turbo.setProgressBarDelay(0);
        global.Turbo.session.drive = true;
    }

    let instantFeedbackInitialized = false;
    let mobileNavInitialized = false;
    let navigationUxInitialized = false;
    let pendingNavElement = null;
    let progressBarInitialized = false;

    function getCsrfToken() {
        const meta = document.querySelector('meta[name="csrf-token"]');
        return meta ? meta.getAttribute('content') : '';
    }

    function ensureCsrfTokens() {
        const token = getCsrfToken();
        if (!token) return;
        document.querySelectorAll('form').forEach((form) => {
            const method = (form.getAttribute('method') || 'get').toLowerCase();
            if (method === 'get') return;
            if (form.querySelector('input[name="csrf_token"]')) return;
            const input = document.createElement('input');
            input.type = 'hidden';
            input.name = 'csrf_token';
            input.value = token;
            form.appendChild(input);
        });
    }

    global.csrfFetch = function(input, init) {
        const opts = Object.assign({ credentials: 'same-origin' }, init || {});
        const method = (opts.method || 'GET').toUpperCase();
        if (!['GET', 'HEAD', 'OPTIONS', 'TRACE'].includes(method)) {
            const headers = new Headers(opts.headers || {});
            if (!headers.has('X-CSRFToken')) {
                const token = getCsrfToken();
                if (token) headers.set('X-CSRFToken', token);
            }
            opts.headers = headers;
        }
        return fetch(input, opts);
    };

    function initFlashAutoHide() {
        document.querySelectorAll('.px-alert').forEach((alertBox) => {
            if (alertBox.dataset.autohideReady === '1') return;
            alertBox.dataset.autohideReady = '1';
            global.setTimeout(() => {
                alertBox.classList.add('is-dismissing');
                global.setTimeout(() => {
                    if (alertBox.parentNode) {
                        alertBox.remove();
                    }
                }, 220);
            }, 1000);
        });
    }

    function initLazyLoading() {
        if ('IntersectionObserver' in global) {
            const imageObserver = new IntersectionObserver((entries, observer) => {
                entries.forEach((entry) => {
                    if (!entry.isIntersecting) return;
                    const img = entry.target;
                    if (img.dataset.src) {
                        img.src = img.dataset.src;
                        img.removeAttribute('data-src');
                        img.classList.add('lazy-loaded');
                    }
                    observer.unobserve(img);
                });
            }, { rootMargin: '50px' });

            document.querySelectorAll('img[data-src]').forEach((img) => {
                imageObserver.observe(img);
            });
            return;
        }

        document.querySelectorAll('img[data-src]').forEach((img) => {
            img.src = img.dataset.src;
            img.removeAttribute('data-src');
        });
    }

    function initInstantFeedback() {
        if (instantFeedbackInitialized) return;
        instantFeedbackInitialized = true;
        const prefetched = new Set();

        function canPrefetchLink(link) {
            if (!link) return false;
            if (link.target === '_blank' || link.hasAttribute('download')) return false;
            if (link.dataset.turbo === 'false') return false;
            const href = link.getAttribute('href') || '';
            return href.startsWith('/') && !href.startsWith('//') && !href.startsWith('/logout');
        }

        function prefetchLink(link) {
            if (!canPrefetchLink(link)) return;
            const href = link.getAttribute('href');
            if (!href || prefetched.has(href)) return;
            if (document.head.querySelector(`link[rel="prefetch"][href="${href}"]`)) {
                prefetched.add(href);
                return;
            }
            const preload = document.createElement('link');
            preload.rel = 'prefetch';
            preload.href = href;
            document.head.appendChild(preload);
            prefetched.add(href);
        }

        document.addEventListener('pointerenter', (event) => {
            const link = event.target.closest('a');
            if (link) prefetchLink(link);
        }, true);

        document.addEventListener('focusin', (event) => {
            const link = event.target.closest('a');
            if (link) prefetchLink(link);
        });

        document.addEventListener('touchstart', (event) => {
            const link = event.target.closest('a');
            if (link) prefetchLink(link);
        }, { passive: true });
    }

    function initNavigationUx() {
        if (navigationUxInitialized) return;
        if (document.body.classList.contains('page-game')) return;
        navigationUxInitialized = true;

        function clearPending() {
            document.body.classList.remove('turbo-loading');
            if (pendingNavElement) {
                pendingNavElement.classList.remove('nav-pending');
                pendingNavElement = null;
            }
        }

        document.addEventListener('click', (event) => {
            const link = event.target.closest('a');
            if (!link) return;
            if (link.dataset.turbo === 'false') return;
            if (link.target === '_blank' || link.hasAttribute('download')) return;
            const href = link.getAttribute('href') || '';
            if (!href.startsWith('/') || href.startsWith('//')) return;
            if (pendingNavElement && pendingNavElement !== link) {
                pendingNavElement.classList.remove('nav-pending');
            }
            pendingNavElement = link;
            pendingNavElement.classList.add('nav-pending');
            document.body.classList.add('turbo-loading');
        }, true);

        document.addEventListener('turbo:before-visit', () => {
            document.body.classList.add('turbo-loading');
        });
        document.addEventListener('turbo:before-fetch-request', () => {
            document.body.classList.add('turbo-loading');
        });
        document.addEventListener('turbo:render', clearPending);
        document.addEventListener('turbo:load', clearPending);
        document.addEventListener('turbo:fetch-request-error', clearPending);
        global.addEventListener('pageshow', clearPending);
    }

    function initMobileNavToggle() {
        if (mobileNavInitialized) return;
        const toggle = document.querySelector('.px-nav-toggle');
        const menu = document.getElementById('mobile-nav-menu');
        if (!toggle || !menu) return;
        mobileNavInitialized = true;
        toggle.addEventListener('click', () => {
            const isOpen = menu.classList.toggle('open');
            toggle.setAttribute('aria-expanded', isOpen ? 'true' : 'false');
        });
    }

    function countWords(text) {
        if (!text) return 0;
        return text.trim().split(/\s+/).filter(Boolean).length;
    }

    function initWordAndFileLimits() {
        if (document.body.dataset.wordLimitBound === '1') return;
        document.body.dataset.wordLimitBound = '1';

        document.addEventListener('submit', (event) => {
            const form = event.target;
            if (!form || form.tagName !== 'FORM') return;
            const fields = form.querySelectorAll('textarea, input[type="text"]');
            for (const field of fields) {
                const max = parseInt(field.dataset.maxWords || '100', 10);
                if (!max) continue;
                const words = countWords(field.value);
                if (words > max) {
                    event.preventDefault();
                    global.alert(`Maximum ${max} words allowed.`);
                    field.focus();
                    return;
                }
            }
        }, true);

        document.addEventListener('change', (event) => {
            const input = event.target;
            if (!input || input.type !== 'file') return;
            const maxBytes = 10 * 1024 * 1024;
            for (const file of input.files || []) {
                if (file.size > maxBytes) {
                    global.alert('File is too large. Maximum 10MB allowed.');
                    input.value = '';
                    return;
                }
            }
        }, true);
    }

    function initConfirmForms() {
        if (!global.TNNOApp || typeof global.TNNOApp.confirmFormSubmission !== 'function') return;
        global.TNNOApp.confirmFormSubmission('form[data-confirm-message]');
    }

    function initProgressBar() {
        if (progressBarInitialized) return;
        progressBarInitialized = true;
        const progressBar = document.getElementById('progress-bar');
        let progress = 0;
        let interval = null;

        function startProgress() {
            if (!progressBar) return;
            global.clearInterval(interval);
            progress = 0;
            progressBar.style.width = '0%';
            progressBar.style.opacity = '1';
            interval = global.setInterval(() => {
                progress += 25;
                if (progress > 85) progress = 85;
                progressBar.style.width = `${progress}%`;
            }, 50);
        }

        function stopProgress() {
            if (!progressBar) return;
            global.clearInterval(interval);
            progressBar.style.width = '100%';
            global.setTimeout(() => {
                progressBar.style.opacity = '0';
                global.setTimeout(() => {
                    progressBar.style.width = '0%';
                }, 100);
            }, 80);
        }

        document.addEventListener('turbo:before-fetch-request', startProgress);
        document.addEventListener('turbo:load', stopProgress);
        document.addEventListener('turbo:frame-load', stopProgress);
        document.addEventListener('turbo:fetch-request-error', stopProgress);
        global.addEventListener('load', stopProgress);
    }

    function initPage() {
        initLazyLoading();
        initInstantFeedback();
        initNavigationUx();
        initMobileNavToggle();
        ensureCsrfTokens();
        initFlashAutoHide();
        initWordAndFileLimits();
        initConfirmForms();
        initProgressBar();
        document.body.classList.remove('turbo-loading');
    }

    document.addEventListener('mouseover', (event) => {
        const link = event.target.closest('a');
        if (!link) return;
        const href = link.getAttribute('href');
        if (!href || !href.startsWith('/') || href.startsWith('//')) return;
        const preload = document.createElement('link');
        preload.rel = 'prefetch';
        preload.href = href;
        document.head.appendChild(preload);
    });

    document.addEventListener('click', (event) => {
        const link = event.target.closest('a');
        if (!link) return;
        if (link.classList.contains('no-tap-fade') || link.closest('.chan-container')) {
            return;
        }
        link.style.opacity = '0.7';
        global.setTimeout(() => {
            link.style.opacity = '1';
        }, 200);
    });

    document.addEventListener('turbo:load', initPage);
    document.addEventListener('turbo:render', initPage);
    document.addEventListener('DOMContentLoaded', initPage);
})(window);
