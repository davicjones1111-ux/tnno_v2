(function(global) {
    'use strict';

    const app = global.TNNOApp || {};
    const storePage = document.querySelector('.store-page');
    if (!storePage) return;

    let currentPage = Number(storePage.dataset.initialPage || 1);
    let isLoading = false;
    let hasMore = (storePage.dataset.initialHasNext || 'false') === 'true';
    let searchQuery = storePage.dataset.initialSearch || '';
    let sellerQuery = storePage.dataset.initialSeller || '';
    let typeFilter = storePage.dataset.initialType || '';
    let sortBy = storePage.dataset.initialSort || 'latest';
    const limit = 12;
    const apiBaseUrl = storePage.dataset.apiUrl;
    const pageBaseUrl = storePage.dataset.pageUrl;

    const feed = document.getElementById('productsFeed');
    const loadingIndicator = document.getElementById('loadingIndicator');
    const endMessage = document.getElementById('endMessage');
    const emptyState = document.getElementById('emptyState');
    const loadMoreContainer = document.getElementById('loadMoreContainer');
    const loadMoreBtn = document.getElementById('loadMoreBtn');
    const productSearch = document.getElementById('productSearch');
    const sellerSearch = document.getElementById('sellerSearch');
    const sortSelect = document.getElementById('sortSelect');
    const clearSearchBtn = document.getElementById('clearSearchBtn');

    function debounce(func, wait) {
        let timeout;
        return function(...args) {
            global.clearTimeout(timeout);
            timeout = global.setTimeout(() => func.apply(this, args), wait);
        };
    }

    function buildApiUrl(page, overrideParams) {
        const params = new URLSearchParams({
            page,
            limit,
            sort: sortBy
        });

        if (typeFilter) params.set('type', typeFilter);
        if (searchQuery) params.set('search', searchQuery);
        if (sellerQuery) params.set('seller', sellerQuery);

        Object.entries(overrideParams || {}).forEach(([key, value]) => {
            if (value === undefined || value === null || value === '') {
                params.delete(key);
            } else {
                params.set(key, value);
            }
        });

        return `${apiBaseUrl}?${params.toString()}`;
    }

    function buildPageUrl(overrideParams) {
        const params = new URLSearchParams();
        if (searchQuery) params.set('search', searchQuery);
        if (sellerQuery) params.set('seller', sellerQuery);
        if (typeFilter) params.set('type', typeFilter);
        if (sortBy && sortBy !== 'latest') params.set('sort', sortBy);

        Object.entries(overrideParams || {}).forEach(([key, value]) => {
            if (value === undefined || value === null || value === '') {
                params.delete(key);
            } else {
                params.set(key, value);
            }
        });

        const qs = params.toString();
        return qs ? `${pageBaseUrl}?${qs}` : pageBaseUrl;
    }

    function createFeedbackChip(text) {
        return app.createElement('span', {
            className: 'feedback-chip',
            text
        });
    }

    function buildSellerNode(product) {
        if (product.seller_username) {
            const sellerLink = app.createElement('a', {
                className: 'seller-link',
                attrs: { href: `/store/seller/${encodeURIComponent(product.seller_id)}` }
            });
            sellerLink.appendChild(app.createElement('span', {
                className: 'seller-avatar',
                text: app.toText(product.seller_username).charAt(0).toUpperCase()
            }));
            sellerLink.appendChild(document.createTextNode(app.toText(product.seller_username)));
            return sellerLink;
        }

        return app.createElement('span', {
            className: 'seller-link',
            text: 'Sold by: Admin'
        });
    }

    function buildProductCard(product) {
        const card = app.createElement('div', {
            className: 'product-card',
            dataset: { productId: product.id }
        });

        const productType = product.product_type || 'digital';
        const imageContainer = app.createElement('div', { className: 'product-image-container' });
        if (product.image_filename) {
            imageContainer.appendChild(app.createElement('img', {
                className: 'product-image',
                attrs: {
                    src: app.mediaUrl(`uploads/merch/${product.image_filename}`),
                    alt: app.toText(product.name),
                    loading: 'lazy'
                }
            }));
        } else {
            const placeholder = app.createElement('div', { className: 'product-image-placeholder' });
            placeholder.appendChild(app.createElement('span', { text: '🎁' }));
            imageContainer.appendChild(placeholder);
        }

        imageContainer.appendChild(app.createElement('div', {
            className: `product-type-badge ${productType}`,
            text: productType
        }));

        if (Number(product.quantity || 0) === 0) {
            imageContainer.appendChild(app.createElement('div', {
                className: 'sold-out-badge',
                text: 'Sold Out'
            }));
        } else if (Number(product.quantity || 0) <= 3) {
            imageContainer.appendChild(app.createElement('div', {
                className: 'low-stock-badge',
                text: `Only ${product.quantity} left!`
            }));
        }

        const content = app.createElement('div', { className: 'product-content' });
        content.appendChild(app.createElement('h3', {
            className: 'product-title',
            text: product.name
        }));

        const description = product.description ? app.toText(product.description).substring(0, 80) : '';
        const descSuffix = product.description && product.description.length > 80 ? '...' : '';
        content.appendChild(app.createElement('p', {
            className: 'product-description',
            text: `${description}${descSuffix}`
        }));

        const sellerWrap = app.createElement('div', { className: 'product-seller' });
        sellerWrap.appendChild(buildSellerNode(product));
        content.appendChild(sellerWrap);

        const feedbackMeta = app.createElement('div', { className: 'product-feedback-meta' });
        const feedback = product.feedback || {};
        const roundedStars = Math.round(Number(feedback.avg_rating || 0));
        let stars = '';
        for (let i = 1; i <= 5; i += 1) {
            stars += i <= roundedStars ? '★' : '☆';
        }
        feedbackMeta.appendChild(createFeedbackChip(`${stars} ${Number(feedback.rating_count || 0)}`));
        feedbackMeta.appendChild(createFeedbackChip(`Review ${Number(feedback.review_count || 0)}`));
        feedbackMeta.appendChild(createFeedbackChip(`Like ${Number(feedback.like_count || 0)}`));
        feedbackMeta.appendChild(createFeedbackChip(`Dislike ${Number(feedback.dislike_count || 0)}`));
        content.appendChild(feedbackMeta);

        const footer = app.createElement('div', { className: 'product-footer' });
        const price = app.createElement('div', { className: 'product-price' });
        price.appendChild(app.createElement('span', { className: 'px-coin' }));
        price.appendChild(app.createElement('span', {
            className: 'price-value',
            text: product.price
        }));
        footer.appendChild(price);

        if (Number(product.quantity || 0) > 0) {
            footer.appendChild(app.createElement('a', {
                className: 'px-btn px-btn-primary px-btn-sm',
                text: 'Buy Now',
                attrs: { href: `/store/product/${product.id}` }
            }));
        } else {
            footer.appendChild(app.createElement('button', {
                className: 'px-btn px-btn-secondary px-btn-sm',
                text: 'Sold Out',
                attrs: { type: 'button', disabled: true }
            }));
        }

        content.appendChild(footer);
        card.appendChild(imageContainer);
        card.appendChild(content);
        return card;
    }

    function renderEmptyState() {
        const empty = app.createElement('div', { className: 'empty-state' });
        empty.appendChild(app.createElement('div', { className: 'empty-icon', text: '🔍' }));
        empty.appendChild(app.createElement('h3', { text: 'No Results' }));
        empty.appendChild(app.createElement('p', {
            className: 'px-text-muted',
            text: 'No products match your search.'
        }));
        feed.appendChild(empty);
    }

    async function loadProducts() {
        if (isLoading || !hasMore) return;

        isLoading = true;
        if (loadingIndicator) loadingIndicator.style.display = 'flex';

        try {
            const response = await fetch(buildApiUrl(currentPage + 1), {
                credentials: 'same-origin'
            });
            if (!response.ok) throw new Error('Failed to load products');

            const data = await response.json();
            if (data.products && data.products.length > 0) {
                renderProducts(data.products);
                currentPage = data.page;
                hasMore = data.has_next;
                if (!hasMore && endMessage) endMessage.style.display = 'block';
            } else {
                hasMore = false;
                if (endMessage) endMessage.style.display = 'block';
            }
        } catch (error) {
            console.error('Error loading products:', error);
            if (loadMoreContainer) loadMoreContainer.style.display = 'block';
        } finally {
            isLoading = false;
            if (loadingIndicator) loadingIndicator.style.display = 'none';
        }
    }

    function renderProducts(products) {
        if (!feed) return;
        products.forEach((product) => feed.appendChild(buildProductCard(product)));
    }

    async function reloadProducts() {
        currentPage = 1;
        hasMore = true;

        feed?.querySelectorAll('.product-card').forEach((card) => card.remove());
        emptyState?.remove();

        if (endMessage) endMessage.style.display = 'none';
        if (loadMoreContainer) loadMoreContainer.style.display = 'none';
        if (loadingIndicator) loadingIndicator.style.display = 'flex';

        try {
            const response = await fetch(buildApiUrl(1), {
                credentials: 'same-origin'
            });
            if (!response.ok) throw new Error('Failed to load products');

            const data = await response.json();
            if (data.products && data.products.length > 0) {
                renderProducts(data.products);
                currentPage = data.page;
                hasMore = data.has_next;
                if (!hasMore && endMessage) endMessage.style.display = 'block';
            } else if (feed) {
                renderEmptyState();
            }
        } catch (error) {
            console.error('Error:', error);
        } finally {
            if (loadingIndicator) loadingIndicator.style.display = 'none';
        }
    }

    const handleProductSearch = debounce((query) => {
        searchQuery = query;
        global.history.pushState({}, '', buildPageUrl({ search: query || undefined, page: undefined }));
        reloadProducts();
    }, 400);

    const handleSellerSearch = debounce((query) => {
        sellerQuery = query;
        global.history.pushState({}, '', buildPageUrl({ seller: query || undefined, page: undefined }));
        reloadProducts();
    }, 400);

    productSearch?.addEventListener('input', function() {
        handleProductSearch(this.value.trim());
    });

    sellerSearch?.addEventListener('input', function() {
        handleSellerSearch(this.value.trim());
    });

    function updateSort() {
        if (!sortSelect) return;
        sortBy = sortSelect.value;
        global.history.pushState({}, '', buildPageUrl({ page: undefined }));
        reloadProducts();
    }

    function clearSearch() {
        if (productSearch) productSearch.value = '';
        searchQuery = '';
        global.history.pushState({}, '', buildPageUrl({ search: undefined, page: undefined }));
        reloadProducts();
    }

    sortSelect?.addEventListener('change', updateSort);
    clearSearchBtn?.addEventListener('click', clearSearch);

    const observer = new IntersectionObserver((entries) => {
        if (entries[0].isIntersecting && hasMore && !isLoading && !searchQuery && !sellerQuery) {
            loadProducts();
        }
    }, { rootMargin: '150px' });

    if (endMessage) observer.observe(endMessage);

    loadMoreBtn?.addEventListener('click', function() {
        this.disabled = true;
        this.textContent = 'Loading...';
        loadProducts().finally(() => {
            this.disabled = false;
            this.textContent = 'Load More Products';
        });
    });

    if (!hasMore && endMessage) {
        endMessage.style.display = 'block';
    }
})(window);
