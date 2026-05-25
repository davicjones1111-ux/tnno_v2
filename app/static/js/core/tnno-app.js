(function(global) {
    'use strict';

    function toText(value) {
        if (value === null || value === undefined) return '';
        return String(value);
    }

    function clearChildren(node) {
        if (!node) return;
        while (node.firstChild) {
            node.removeChild(node.firstChild);
        }
    }

    function appendChildren(node, children) {
        if (!node || !children) return node;
        const list = Array.isArray(children) ? children : [children];
        list.forEach((child) => {
            if (child === null || child === undefined) return;
            if (typeof child === 'string') {
                node.appendChild(document.createTextNode(child));
                return;
            }
            node.appendChild(child);
        });
        return node;
    }

    function createElement(tagName, options) {
        const opts = options || {};
        const element = document.createElement(tagName);
        if (opts.className) {
            element.className = opts.className;
        }
        if (opts.text !== undefined) {
            element.textContent = toText(opts.text);
        }
        if (opts.html !== undefined) {
            setSanitizedHTML(element, opts.html, opts.sanitizeOptions);
        }
        if (opts.attrs) {
            Object.entries(opts.attrs).forEach(([key, value]) => {
                if (value === null || value === undefined || value === false) return;
                element.setAttribute(key, value === true ? '' : toText(value));
            });
        }
        if (opts.dataset) {
            Object.entries(opts.dataset).forEach(([key, value]) => {
                if (value === null || value === undefined) return;
                element.dataset[key] = toText(value);
            });
        }
        if (opts.children) {
            appendChildren(element, opts.children);
        }
        return element;
    }

    function sanitizeHTML(html, options) {
        const raw = toText(html);
        if (global.DOMPurify && typeof global.DOMPurify.sanitize === 'function') {
            return global.DOMPurify.sanitize(raw, options || { USE_PROFILES: { html: true } });
        }

        const template = document.createElement('template');
        template.textContent = raw;
        return template.innerHTML;
    }

    function setSanitizedHTML(element, html, options) {
        if (!element) return;
        element.innerHTML = sanitizeHTML(html, options);
    }

    function parseJsonDataset(element, datasetKey, fallbackValue) {
        if (!element) return fallbackValue;
        const raw = element.dataset ? element.dataset[datasetKey] : '';
        if (!raw) return fallbackValue;
        try {
            return JSON.parse(raw);
        } catch (_error) {
            return fallbackValue;
        }
    }

    function safeUrl(value, opts) {
        const options = Object.assign({
            allowRelative: true,
            allowData: false,
            sameOriginOnly: false,
        }, opts || {});
        const raw = toText(value).trim();
        if (!raw) return '';
        if (options.allowRelative && raw.startsWith('/') && !raw.startsWith('//')) {
            return raw;
        }

        try {
            const url = new URL(raw, global.location.origin);
            const protocol = url.protocol.toLowerCase();
            const isData = protocol === 'data:';
            const isHttp = protocol === 'http:' || protocol === 'https:';
            if (isData && options.allowData) {
                return url.toString();
            }
            if (!isHttp) {
                return '';
            }
            if (options.sameOriginOnly && url.origin !== global.location.origin) {
                return '';
            }
            return url.toString();
        } catch (_error) {
            return '';
        }
    }

    function mediaUrl(value) {
        const raw = toText(value).trim();
        if (!raw) return '';
        if (/^https?:\/\//i.test(raw)) {
            return safeUrl(raw);
        }
        if (raw.startsWith('/static/') || raw.startsWith('/uploads/')) {
            return safeUrl(raw);
        }
        if (raw.startsWith('uploads/')) {
            return `/static/${raw}`;
        }
        return `/static/${raw.replace(/^\/+/, '')}`;
    }

    function confirmFormSubmission(selector, message) {
        document.querySelectorAll(selector).forEach((form) => {
            if (form.dataset.confirmBound === '1') return;
            form.dataset.confirmBound = '1';
            form.addEventListener('submit', (event) => {
                const confirmMessage = message || form.dataset.confirmMessage || 'Are you sure?';
                if (!global.confirm(confirmMessage)) {
                    event.preventDefault();
                }
            });
        });
    }

    global.TNNOApp = Object.assign({}, global.TNNOApp || {}, {
        appendChildren,
        clearChildren,
        confirmFormSubmission,
        createElement,
        mediaUrl,
        parseJsonDataset,
        safeUrl,
        sanitizeHTML,
        setSanitizedHTML,
        toText,
    });
})(window);
